"""判定ロジック本体(仕様書3章=値下げ / 4章=値上げ / 4.3=ハンチング防止)。

レポートのみモードの中核。商品ごとに以下を順に見て 1件の Candidate を返す:

  1. 除外フラグ(仕様書6.5)          → skip
  2. 設定エラー / 赤字 / 二重価格表示   → warn
  3. 新商品保護期間(仕様書 M)         → skip
  4. 値上げシグナル(急売れ / 仕様書4章) → markup / needs_approval
  5. 値下げシグナル(売れない / 仕様書3章)→ markdown / floor_reached / needs_approval
  6. どのシグナルも立たない            → skip(据え置き)

クールダウン・週次上限・累計変動上限といったガードは、シグナルが立った後に
方向(up/down)が確定してから適用する(方向転換ペナルティのため方向が必要)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import DEFAULT_GROUPS, GroupConfig, get_group_config
from .guardrails import (
    ceiling_price,
    clamp_markdown,
    clamp_markup,
    exceeds_cumulative_cap,
    floor_price,
    round_price,
)
from .models import Candidate, Order, PriceChange, Product


@dataclass
class EvalContext:
    """1回の判定実行に必要な、商品横断の入力をまとめた入れ物。

    リスト群はコードごとに引けるよう辞書化して持つ(data_loader.build_context 参照)。
    """

    as_of: date
    orders_by_code: dict[str, list[Order]] = field(default_factory=dict)
    stockouts_by_code: dict[str, set[date]] = field(default_factory=dict)
    changes_by_code: dict[str, list[PriceChange]] = field(default_factory=dict)
    event_days: set[date] = field(default_factory=set)
    # 商品ごとの絶対下限単価(仕様書3.2)。無ければ原価ベース下限だけを使う。
    absolute_floor_by_code: dict[str, int] = field(default_factory=dict)
    # 商品ごとの絶対上限単価。無ければ基準価格(定価)だけを上限として使う。
    absolute_ceiling_by_code: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# 小さな集計ヘルパ
# --------------------------------------------------------------------------- #
def _valid_qty_in_range(orders: list[Order], start: date, end: date) -> int:
    """[start, end](両端含む)の有効注文の数量合計。"""
    return sum(o.quantity for o in orders if o.valid and start <= o.ordered_on <= end)


def _markdown_stats(
    orders: list[Order], as_of: date, lookback_days: int, stockouts: set[date]
) -> tuple[int, int, date]:
    """直近 lookback_days 日(固定窓)の (販売可能日数, 有効注文数, 窓開始日) を返す。

    在庫切れ日は「販売可能日」から除外する(在庫0で売れないのは需要不足ではないため)。
    販売可能日が少なすぎる場合は呼び出し側で判定を保留する。
    """
    window_start = as_of - timedelta(days=lookback_days - 1)
    sellable_days = sum(
        1 for i in range(lookback_days) if (as_of - timedelta(days=i)) not in stockouts
    )
    orders_in_window = _valid_qty_in_range(orders, window_start, as_of)
    return sellable_days, orders_in_window, window_start


def _daily_baseline(orders: list[Order], as_of: date, window_days: int, event_days: set[date]) -> float:
    """ベースライン日次平均(仕様書4章)。イベント日は分子・分母とも除外。"""
    total = 0
    sellable_days = 0
    for i in range(window_days):
        d = as_of - timedelta(days=i)
        if d in event_days:
            continue
        sellable_days += 1
        total += _valid_qty_in_range(orders, d, d)
    if sellable_days == 0:
        return 0.0
    return total / sellable_days


def _recent_window(orders: list[Order], as_of: date, days: int, event_days: set[date]) -> tuple[int, int]:
    """直近 days 日の有効注文数(イベント日除外)と、対象になった非イベント日数。"""
    total = 0
    n_days = 0
    for i in range(days):
        d = as_of - timedelta(days=i)
        if d in event_days:
            continue
        n_days += 1
        total += _valid_qty_in_range(orders, d, d)
    return total, n_days


def _ups_last_week(changes: list[PriceChange], as_of: date) -> int:
    """直近7日以内の値上げ回数(週次上限 markup_max_per_week 用)。"""
    return sum(1 for c in changes if c.direction == "up" and 0 <= (as_of - c.changed_on).days < 7)


def _cooldown_block(
    changes: list[PriceChange], as_of: date, cfg: GroupConfig, proposed_direction: str
) -> tuple[PriceChange, int, int] | None:
    """クールダウン中なら (直前変更, 経過日数, 有効クールダウン日数) を返す。空けていれば None。

    直前と逆方向の変更(ハンチング)なら direction_change_penalty 倍に伸ばす(仕様書4.3)。
    """
    if not changes:
        return None
    last = max(changes, key=lambda c: c.changed_on)
    days_since = (as_of - last.changed_on).days
    cooldown = cfg.cooldown_days
    if last.direction in ("up", "down") and last.direction != proposed_direction:
        cooldown = int(math.ceil(cooldown * cfg.direction_change_penalty))
    if days_since < cooldown:
        return last, days_since, cooldown
    return None


# --------------------------------------------------------------------------- #
# Candidate 組み立て
# --------------------------------------------------------------------------- #
def _skip(product: Product, reason: str, details: dict | None = None) -> Candidate:
    return Candidate(product.code, "skip", product.current_price, None, reason, details or {})


def _warn(product: Product, reason: str, details: dict | None = None) -> Candidate:
    return Candidate(product.code, "warn", product.current_price, None, reason, details or {})


def _build_markdown(product: Product, cfg: GroupConfig, floor: int, details: dict) -> Candidate:
    """値下げの提案価格を作って下限・端数・累計上限を適用する。"""
    if cfg.markdown_fixed_yen is not None:
        raw = product.current_price - cfg.markdown_fixed_yen
    else:
        raw = round(product.current_price * (1.0 - cfg.markdown_rate))
    proposed = round_price(int(raw), cfg.rounding_mode, direction="down")
    final, hit_floor = clamp_markdown(proposed, floor)

    details = {**details, "floor": floor, "raw_proposed": int(raw), "rounded": proposed}

    # 端数丸めや下限張り付きで、現在価格以上にしかできない = もう下げ余地がない。
    if final >= product.current_price:
        return Candidate(
            product.code, "floor_reached", product.current_price, floor,
            f"下限({floor:,}円)に到達済み。これ以上の値下げ不可", details,
        )
    if hit_floor:
        return Candidate(
            product.code, "floor_reached", product.current_price, final,
            f"下限({floor:,}円)まで値下げ(下限張り付き)", details,
        )
    if exceeds_cumulative_cap(final, product.base_price, cfg):
        return Candidate(
            product.code, "needs_approval", product.current_price, final,
            f"値下げ後が基準価格の±{int(cfg.cumulative_cap_rate * 100)}%を超過。承認待ち", details,
        )
    orders = details.get("orders_in_window")
    return Candidate(
        product.code, "markdown", product.current_price, final,
        f"直近{details.get('sellable_days')}販売可能日で有効注文{orders}件 → 値下げ", details,
    )


def _build_markup(product: Product, cfg: GroupConfig, ceiling: int, details: dict) -> Candidate:
    """値上げの提案価格を作って上限(基準価格 or 商品ごとの絶対上限)・端数・累計上限を適用する。"""
    raw = round(product.current_price * (1.0 + cfg.markup_rate))
    proposed = round_price(int(raw), cfg.rounding_mode, direction="up")
    final, hit_ceiling = clamp_markup(proposed, ceiling)

    details = {**details, "ceiling": ceiling, "raw_proposed": int(raw), "rounded": proposed}

    # 端数丸め下げや上限張り付きで、現在価格以下にしかできない = 上げ余地がない。
    if final <= product.current_price:
        return Candidate(
            product.code, "skip", product.current_price, None,
            f"上限({ceiling:,}円)付近で値上げ余地なし", details,
        )
    if exceeds_cumulative_cap(final, product.base_price, cfg):
        return Candidate(
            product.code, "needs_approval", product.current_price, final,
            f"値上げ後が基準価格の±{int(cfg.cumulative_cap_rate * 100)}%を超過。承認待ち", details,
        )
    tail = "(上限に到達)" if hit_ceiling else ""
    mult = details.get("baseline_multiple")
    mult_txt = f"{mult:.1f}倍" if isinstance(mult, (int, float)) else "急増"
    return Candidate(
        product.code, "markup", product.current_price, final,
        f"直近{cfg.markup_lookback_days}日の売れ行きがベースライン比{mult_txt} → 値上げ{tail}", details,
    )


# --------------------------------------------------------------------------- #
# メイン
# --------------------------------------------------------------------------- #
def evaluate_product(product: Product, cfg: GroupConfig, ctx: EvalContext) -> Candidate:
    """1商品を判定して Candidate を1件返す。"""
    # 1) 除外フラグ(仕様書6.5)
    reasons = product.exclusion_reasons()
    if reasons:
        return _skip(product, "対象外: " + " / ".join(reasons), {"exclusions": reasons})

    # 2) 下限・上限計算(設定不正はここで warn に落とす)
    absolute_floor = ctx.absolute_floor_by_code.get(product.code)
    try:
        floor = floor_price(product, cfg, absolute_floor)
    except ValueError as e:
        return _warn(product, f"下限価格を計算できません: {e}")

    absolute_ceiling = ctx.absolute_ceiling_by_code.get(product.code)
    ceiling = ceiling_price(product, absolute_ceiling)
    if floor > ceiling:
        return _warn(
            product,
            f"下限({floor:,}円)が上限({ceiling:,}円)を超えています。商品ごとの"
            "最低売価・最高売価の設定を確認してください",
            {"floor": floor, "ceiling": ceiling},
        )

    # 2') 景表法リスク(二重価格表示中)は自動変更せず必ず人手で確認
    if product.has_reference_price_display:
        return _warn(
            product,
            "二重価格(参考価格/OFF率)表示中。価格変更は景表法リスクのため表示見直し後に",
            {"floor": floor},
        )

    # 2'') 現在価格が下限割れ = 送料負け/赤字の疑い(仕様書3.2 の事故対策)
    if product.current_price < floor:
        return _warn(
            product,
            f"現在価格 {product.current_price:,}円 < 下限 {floor:,}円。赤字/送料負けの疑い",
            {"floor": floor},
        )

    # 2''') 現在価格が上限超え = 商品ごとの最高売価の設定ミスの疑い
    if product.current_price > ceiling:
        return _warn(
            product,
            f"現在価格 {product.current_price:,}円 > 上限 {ceiling:,}円。"
            "最高売価の設定を確認してください",
            {"ceiling": ceiling},
        )

    # 3) 新商品保護期間(仕様書 M)
    age_days = (ctx.as_of - product.registered_on).days
    if age_days < cfg.new_product_protection_days:
        return _skip(
            product,
            f"新商品保護期間中(登録から{age_days}日 < {cfg.new_product_protection_days}日)",
            {"age_days": age_days},
        )

    orders = ctx.orders_by_code.get(product.code, [])
    stockouts = ctx.stockouts_by_code.get(product.code, set())
    changes = ctx.changes_by_code.get(product.code, [])

    # 4) 値上げシグナル(急売れ / 仕様書4章)
    baseline = _daily_baseline(orders, ctx.as_of, cfg.baseline_window_days, ctx.event_days)
    recent, n_recent_days = _recent_window(orders, ctx.as_of, cfg.markup_lookback_days, ctx.event_days)
    expected = baseline * n_recent_days
    is_spike = recent >= cfg.markup_baseline_multiple * expected and recent >= cfg.markup_absolute_min_orders
    if is_spike:
        mult = (recent / expected) if expected > 0 else float("inf")
        markup_details = {
            "recent_orders": recent,
            "baseline_per_day": round(baseline, 3),
            "expected_orders": round(expected, 2),
            "baseline_multiple": (None if math.isinf(mult) else round(mult, 2)),
        }
        # 週次上限(仕様書4章 markup_max_per_week)
        ups = _ups_last_week(changes, ctx.as_of)
        if ups >= cfg.markup_max_per_week:
            return _skip(
                product,
                f"直近7日で値上げ済み({ups}/{cfg.markup_max_per_week}回)。週次上限のため据え置き",
                markup_details,
            )
        # クールダウン(方向=up)
        block = _cooldown_block(changes, ctx.as_of, cfg, "up")
        if block:
            last, since, cd = block
            return _skip(
                product,
                f"クールダウン中(前回{last.direction}変更から{since}日 < {cd}日)。値上げ見送り",
                markup_details,
            )
        return _build_markup(product, cfg, ceiling, markup_details)

    # 5) 値下げシグナル(売れない / 仕様書3章)
    sellable, orders_in_window, window_start = _markdown_stats(
        orders, ctx.as_of, cfg.markdown_lookback_days, stockouts
    )
    markdown_details = {
        "sellable_days": sellable,
        "orders_in_window": orders_in_window,
        "window_start": window_start.isoformat(),
    }

    # 在庫切れが多く販売可能日が足りないと「売れない」と断定できない → 判定保留
    min_sellable = math.ceil(cfg.markdown_lookback_days * cfg.markdown_min_sellable_ratio)
    if sellable < min_sellable:
        return _skip(
            product,
            f"販売可能日が不足({sellable}/{cfg.markdown_lookback_days}日、必要{min_sellable}日)。"
            "在庫切れ多く判定保留",
            markdown_details,
        )

    if orders_in_window <= cfg.markdown_order_threshold:
        # クールダウン(方向=down)
        block = _cooldown_block(changes, ctx.as_of, cfg, "down")
        if block:
            last, since, cd = block
            return _skip(
                product,
                f"クールダウン中(前回{last.direction}変更から{since}日 < {cd}日)。値下げ見送り",
                markdown_details,
            )
        return _build_markdown(product, cfg, floor, markdown_details)

    # 6) どのシグナルも立たない = 据え置き
    return _skip(product, "判定シグナルなし(据え置き)", markdown_details)


def evaluate_all(
    products: list[Product],
    ctx: EvalContext,
    groups: dict[str, GroupConfig] | None = None,
) -> list[Candidate]:
    """商品リスト全体を判定。グループ設定は config.get_group_config で解決する。"""
    table = groups or DEFAULT_GROUPS
    results: list[Candidate] = []
    for product in products:
        cfg = get_group_config(product.group, table)
        results.append(evaluate_product(product, cfg, ctx))
    return results
