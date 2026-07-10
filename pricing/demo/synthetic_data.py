"""実商品(名称・現売価)に合成の売れ行きデータを重ねて判定エンジンに通す。

仕様書5章(Yahoo注文API連携)が未着手のため、実際の注文履歴・在庫切れ日は
取得できない。そこで [[scrape_client_products]] で取ってきた実商品名・実売価
に対して、`pricing.data_loader.make_dummy_dataset` と同じ判定分岐を一通り
踏むよう合成データを割り当てる。どの商品がどのシナリオを演じているかは
`code_to_scenario` で返すので、デモ画面側で「これは合成データです」と
併記できるようにしてある(実売れ行きだと誤解されないため)。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from ..data_loader import build_context
from ..guardrails import floor_price
from ..config import DEFAULT_GROUPS
from ..models import Order, PriceChange, Product, StockOutDay
from ..rules import EvalContext
from .scrape_client_products import ScrapedProduct

# シナリオ名 -> 表示ラベル(仕様書上の判定分岐を一通り網羅)
SCENARIO_LABELS: dict[str, str] = {
    "markdown": "値下げ候補(直近売れず)",
    "floor_reached": "値下げ→下限張り付き",
    "markup": "値上げ候補(急売れ)",
    "needs_approval_markup": "値上げだが承認待ち(基準価格から乖離大)",
    "needs_approval_markdown": "値下げだが承認待ち(基準価格から乖離大)",
    "cooldown": "クールダウン中(直近価格変更あり)",
    "new_product": "新商品保護期間中",
    "excluded": "除外フラグ対象",
    "loss_warn": "赤字/送料負け警告",
    "reference_price_warn": "二重価格表示 警告",
    "stay": "据え置き(シグナルなし)",
    "stockout_pending": "在庫切れ多く判定保留",
}
SCENARIO_ORDER = list(SCENARIO_LABELS.keys())


def _cost_for_floor_ratio(price: int, ratio: float, shipping: int, denom: float) -> int:
    """floor_price() が概ね price*ratio になるよう cost を逆算する。"""
    target_floor = max(price * ratio, 1)
    cost = round(target_floor * denom) - shipping
    return max(int(cost), 1)


@dataclass
class SyntheticProduct:
    product: Product
    scenario: str


def _default_denom() -> float:
    cfg = DEFAULT_GROUPS["default"]
    return 1.0 - cfg.target_min_margin_rate - cfg.yahoo_fee_rate - cfg.point_fund_rate


def _build_one(
    sp: ScrapedProduct, scenario: str, as_of: date, rng: random.Random, denom: float
) -> tuple[Product, list[Order], list[StockOutDay], list[PriceChange]]:
    price = sp.actual_price or sp.list_price or 1000
    # 実際の定価をそのまま使うと、店舗側の値引き幅が大きい商品では
    # シナリオと無関係に累計変動上限(±20%)へ抵触してしまうことがあるため、
    # 定価との差が小さい(12%未満)場合だけ実データを使い、それ以外は
    # シナリオの意図が伝わるよう控えめな合成の定価にフォールバックする。
    if price < sp.list_price <= price * 1.12:
        base_price = sp.list_price
    else:
        base_price = round(price * 1.1)
    old_registered = as_of - timedelta(days=400)
    shipping = rng.choice([0, 0, 100, 200])

    orders: list[Order] = []
    stockouts: list[StockOutDay] = []
    changes: list[PriceChange] = []
    flags: dict = {}
    registered_on = old_registered
    cost_ratio = 0.45  # 既定: 下限まで十分な余裕がある原価率

    if scenario == "markdown":
        pass  # 注文0件(デフォルト) → そのまま値下げ候補になる

    elif scenario == "floor_reached":
        cost_ratio = 0.985  # 現在価格のすぐ下に下限が来るよう原価を高めに

    elif scenario == "markup":
        for d_off in (0, 2):
            orders.append(Order(sp.item_id, as_of - timedelta(days=d_off), quantity=rng.randint(3, 5)))

    elif scenario == "needs_approval_markup":
        base_price = round(price / 0.6)  # 現在価格が定価の6割 = 大幅値引き中
        for d_off in (0, 2):
            orders.append(Order(sp.item_id, as_of - timedelta(days=d_off), quantity=rng.randint(3, 5)))

    elif scenario == "needs_approval_markdown":
        base_price = round(price / 0.75)  # 現在価格が定価の75% = すでにかなり値引き中

    elif scenario == "cooldown":
        changes.append(PriceChange(sp.item_id, as_of - timedelta(days=3), "down", round(price * 1.05), price))

    elif scenario == "new_product":
        registered_on = as_of - timedelta(days=10)

    elif scenario == "excluded":
        flag = rng.choice(
            ["is_set_or_lucky_bag", "is_maker_fixed_price", "is_preorder", "season_paused"]
        )
        flags[flag] = True

    elif scenario == "loss_warn":
        cost_ratio = 1.15  # 原価ベース下限が現在価格を上回る = 赤字状態

    elif scenario == "reference_price_warn":
        flags["has_reference_price_display"] = True

    elif scenario == "stay":
        for d_off in (5, 8, 11):
            orders.append(Order(sp.item_id, as_of - timedelta(days=d_off), quantity=1))

    elif scenario == "stockout_pending":
        for i in range(1, 14):
            stockouts.append(StockOutDay(sp.item_id, as_of - timedelta(days=i)))

    cost = _cost_for_floor_ratio(price, cost_ratio, shipping, denom)

    product = Product(
        code=sp.item_id,
        current_price=price,
        base_price=max(base_price, price),
        registered_on=registered_on,
        stock=0 if sp.has_no_stock else rng.randint(3, 40),
        cost=cost,
        shipping_cost=shipping,
        **flags,
    )
    return product, orders, stockouts, changes


def build_synthetic_dataset(
    scraped: list[ScrapedProduct],
    as_of: date,
    seed: int = 0,
    absolute_floors: dict[str, int] | None = None,
    absolute_ceilings: dict[str, int] | None = None,
) -> tuple[list[Product], EvalContext, dict[str, str]]:
    """実商品リストにシナリオを割り当て、判定エンジンにそのまま渡せる形にする。

    absolute_floors / absolute_ceilings は、画面で個別設定した商品ごとの
    最低売価・最高売価(pricing/demo/product_bounds.py 参照)。指定した商品は
    シナリオ由来の原価/定価に関わらず、この値が下限・上限として優先される。

    戻り値: (Product一覧, EvalContext, {商品コード: シナリオ名})
    """
    rng = random.Random(seed)
    shuffled = list(scraped)
    rng.shuffle(shuffled)

    denom = _default_denom()
    event_days = {as_of - timedelta(days=1)}

    products: list[Product] = []
    all_orders: list[Order] = []
    all_stockouts: list[StockOutDay] = []
    all_changes: list[PriceChange] = []
    code_to_scenario: dict[str, str] = {}

    for i, sp in enumerate(shuffled):
        scenario = SCENARIO_ORDER[i % len(SCENARIO_ORDER)]
        product, orders, stockouts, changes = _build_one(sp, scenario, as_of, rng, denom)
        products.append(product)
        all_orders += orders
        all_stockouts += stockouts
        all_changes += changes
        code_to_scenario[product.code] = scenario

    ctx = build_context(
        as_of, all_orders, all_stockouts, all_changes, event_days,
        absolute_floors=absolute_floors, absolute_ceilings=absolute_ceilings,
    )
    return products, ctx, code_to_scenario
