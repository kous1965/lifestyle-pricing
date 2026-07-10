"""入力データの組み立て(仕様書2章)と、動作確認用のダミーデータ。

実運用では Yahoo注文API / スプレッドシートからこれらを構築するが、まずは
- build_context()      : リスト群を EvalContext(コード別辞書)に整形
- make_dummy_dataset() : 各判定分岐を1件ずつ踏むダミー一式

を用意して、`python -m pricing.data_loader` でレポート出力まで通せるようにする。
"""

from __future__ import annotations

from datetime import date, timedelta

from .models import Order, PriceChange, Product, StockOutDay
from .rules import EvalContext


def build_context(
    as_of: date,
    orders: list[Order],
    stockouts: list[StockOutDay],
    changes: list[PriceChange],
    event_days: set[date] | None = None,
    absolute_floors: dict[str, int] | None = None,
    absolute_ceilings: dict[str, int] | None = None,
) -> EvalContext:
    """フラットなリスト群を、コード別に引ける EvalContext に整形する。"""
    orders_by_code: dict[str, list[Order]] = {}
    for o in orders:
        orders_by_code.setdefault(o.code, []).append(o)

    stockouts_by_code: dict[str, set[date]] = {}
    for s in stockouts:
        stockouts_by_code.setdefault(s.code, set()).add(s.day)

    changes_by_code: dict[str, list[PriceChange]] = {}
    for c in changes:
        changes_by_code.setdefault(c.code, []).append(c)

    return EvalContext(
        as_of=as_of,
        orders_by_code=orders_by_code,
        stockouts_by_code=stockouts_by_code,
        changes_by_code=changes_by_code,
        event_days=event_days or set(),
        absolute_floor_by_code=absolute_floors or {},
        absolute_ceiling_by_code=absolute_ceilings or {},
    )


def make_dummy_dataset(
    as_of: date = date(2026, 7, 10),
) -> tuple[list[Product], EvalContext]:
    """各判定分岐を1件ずつ踏むダミー一式を返す。

    網羅する分岐:
      P-DOWN   値下げ候補(14日売れず)
      P-FLOOR  値下げ→下限張り付き(floor_reached)
      P-UP     値上げ候補(急売れ)
      P-APPUP  値上げだが累計上限超で承認待ち(needs_approval)
      P-COOL   シグナルは立つがクールダウン中で見送り(skip)
      P-NEW    新商品保護期間中(skip)
      P-EXCL   除外フラグ(skip)
      P-LOSS   現在価格が下限割れ(warn)
      P-REF    二重価格表示中(warn)
      P-STAY   シグナルなしで据え置き(skip)
      P-STOCK  在庫切れ多く販売可能日不足で保留(skip)
    """
    old = as_of - timedelta(days=400)          # 保護期間はとうに明けている登録日
    products: list[Product] = []
    orders: list[Order] = []
    stockouts: list[StockOutDay] = []
    changes: list[PriceChange] = []
    event_days: set[date] = {as_of - timedelta(days=1)}  # 直近のYahooイベント日

    # --- 値下げ候補: 直近ずっと有効注文0件、在庫あり、下限までは余裕あり ---------
    products.append(Product("P-DOWN", current_price=1980, base_price=2200,
                            registered_on=old, stock=12, cost=900, shipping_cost=200))

    # --- 下限張り付き: 現在価格は下限より上だが、少し下げると下限に当たる ---------
    #     floor≈1,555 に対し current=1,600。3%下げると下限へクランプされる。
    products.append(Product("P-FLOOR", current_price=1600, base_price=1700,
                            registered_on=old, stock=8, cost=1000, shipping_cost=150))

    # --- 値上げ候補: ベースラインほぼ0に対して直近数日で急売れ、基準価格に余裕 ----
    products.append(Product("P-UP", current_price=1980, base_price=2200,
                            registered_on=old, stock=30, cost=800, shipping_cost=0))
    for d_off in (0, 2):                         # 直近3日窓の非イベント日(as_of, as_of-2)に各3件
        orders += [Order("P-UP", as_of - timedelta(days=d_off), quantity=3)]

    # --- 承認待ち: 急売れだが定価から大きく値引き中で、値上げ後も±20%枠を超える ---
    #     base(定価)=5,000 に対し current=3,000。上げても基準価格から36%乖離 → 承認待ち。
    products.append(Product("P-APPUP", current_price=3000, base_price=5000,
                            registered_on=old, stock=25, cost=1200, shipping_cost=0))
    for d_off in (0, 2):
        orders += [Order("P-APPUP", as_of - timedelta(days=d_off), quantity=3)]

    # --- クールダウン中: 値下げシグナルは立つが3日前に値下げ済み ----------------
    products.append(Product("P-COOL", current_price=1800, base_price=2000,
                            registered_on=old, stock=10, cost=800, shipping_cost=100))
    changes.append(PriceChange("P-COOL", as_of - timedelta(days=3), "down", 1900, 1800))

    # --- 新商品保護期間中: 登録から10日 ---------------------------------------
    products.append(Product("P-NEW", current_price=2500, base_price=2800,
                            registered_on=as_of - timedelta(days=10), stock=20, cost=1000))

    # --- 除外フラグ: 福袋 ------------------------------------------------------
    products.append(Product("P-EXCL", current_price=5000, base_price=5000,
                            registered_on=old, stock=5, cost=2500, is_set_or_lucky_bag=True))

    # --- 赤字警告: 現在価格が原価ベース下限を割っている -------------------------
    products.append(Product("P-LOSS", current_price=900, base_price=1500,
                            registered_on=old, stock=6, cost=1000, shipping_cost=100))

    # --- 二重価格警告: 参考価格/OFF率を表示中 ---------------------------------
    products.append(Product("P-REF", current_price=1980, base_price=2500,
                            registered_on=old, stock=9, cost=800,
                            has_reference_price_display=True))

    # --- 据え置き: そこそこ売れていて値下げも急売れも当たらない -----------------
    products.append(Product("P-STAY", current_price=2200, base_price=2400,
                            registered_on=old, stock=15, cost=1000, shipping_cost=0))
    for d_off in (5, 8, 11):                     # 直近14日に細く売れ、直近3日は無し(急売れでない)
        orders += [Order("P-STAY", as_of - timedelta(days=d_off), quantity=1)]

    # --- 保留: 直近14日の大半が在庫切れで販売可能日が足りない -------------------
    products.append(Product("P-STOCK", current_price=1700, base_price=2000,
                            registered_on=old, stock=0, cost=700, shipping_cost=100))
    for i in range(1, 14):                        # 直近14日中、当日以外はほぼ在庫0
        stockouts.append(StockOutDay("P-STOCK", as_of - timedelta(days=i)))

    ctx = build_context(as_of, orders, stockouts, changes, event_days)
    return products, ctx


def _demo() -> None:
    """`python -m pricing.data_loader` 用のデモ。ダミーデータでレポートを出す。"""
    from . import report
    from .rules import evaluate_all

    as_of = date(2026, 7, 10)
    products, ctx = make_dummy_dataset(as_of)
    candidates = evaluate_all(products, ctx)
    print(report.render_text(candidates, as_of))
    print("---- CSV ----")
    print(report.render_csv(candidates))


if __name__ == "__main__":
    _demo()
