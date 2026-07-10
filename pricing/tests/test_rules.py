"""判定ロジック(rules)のテスト。個別分岐 + ダミーデータの網羅確認。"""

from __future__ import annotations

from datetime import date, timedelta

from pricing.config import GroupConfig
from pricing.data_loader import build_context, make_dummy_dataset
from pricing.models import Order, PriceChange, Product, StockOutDay
from pricing.rules import EvalContext, evaluate_all, evaluate_product

AS_OF = date(2026, 7, 10)
OLD = AS_OF - timedelta(days=400)


def _product(**kw) -> Product:
    base = dict(code="X", current_price=2000, base_price=2400,
                registered_on=OLD, stock=10, cost=800, shipping_cost=0)
    base.update(kw)
    return Product(**base)


def _ctx(**kw) -> EvalContext:
    base = dict(as_of=AS_OF)
    base.update(kw)
    return EvalContext(**base)


def _eval(product, ctx=None, cfg=None):
    return evaluate_product(product, cfg or GroupConfig(), ctx or _ctx())


# ---- 除外・警告・保護 ------------------------------------------------------ #
def test_excluded_product_skips():
    c = _eval(_product(is_set_or_lucky_bag=True))
    assert c.action == "skip"
    assert "福袋" in c.reason


def test_reference_price_display_warns():
    c = _eval(_product(has_reference_price_display=True))
    assert c.action == "warn"
    assert "二重価格" in c.reason


def test_below_floor_warns():
    # cost 900 + ship 200 → floor 1487。current 900 は割れている。
    c = _eval(_product(current_price=900, cost=900, shipping_cost=200))
    assert c.action == "warn"
    assert "赤字" in c.reason or "下限" in c.reason


def test_new_product_protection_skips():
    c = _eval(_product(registered_on=AS_OF - timedelta(days=10)))
    assert c.action == "skip"
    assert "新商品保護" in c.reason


def test_floor_calc_error_warns():
    cfg = GroupConfig(target_min_margin_rate=0.6, yahoo_fee_rate=0.4, point_fund_rate=0.1)
    c = _eval(_product(), cfg=cfg)
    assert c.action == "warn"
    assert "下限価格を計算できません" in c.reason


# ---- 値下げ --------------------------------------------------------------- #
def test_markdown_when_no_sales():
    p = _product(current_price=1980, base_price=2200, cost=900, shipping_cost=200)
    c = _eval(p)  # 注文なし・在庫切れなし → 14日売れず
    assert c.action == "markdown"
    assert c.proposed_price is not None and c.proposed_price < p.current_price


def test_recent_sale_blocks_markdown():
    p = _product(current_price=1980, base_price=2200, cost=900, shipping_cost=200)
    ctx = _ctx(orders_by_code={"X": [Order("X", AS_OF - timedelta(days=2))]})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "skip"
    assert "据え置き" in c.reason


def test_markdown_clamped_to_floor():
    # floor≈1555。current 1600 を3%下げると下限に張り付く。
    p = _product(current_price=1600, base_price=1700, cost=1000, shipping_cost=150)
    c = _eval(p)
    assert c.action == "floor_reached"
    assert c.proposed_price == 1555


def test_stockouts_hold_markdown():
    p = _product(current_price=1700, cost=700, shipping_cost=100)
    stockouts = {AS_OF - timedelta(days=i) for i in range(1, 14)}  # 13/14日が在庫切れ
    ctx = _ctx(stockouts_by_code={"X": stockouts})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "skip"
    assert "販売可能日が不足" in c.reason


def test_markdown_cooldown_blocks():
    p = _product(current_price=1980, base_price=2200, cost=900, shipping_cost=200)
    ctx = _ctx(changes_by_code={"X": [PriceChange("X", AS_OF - timedelta(days=3), "down", 2100, 1980)]})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "skip"
    assert "クールダウン" in c.reason


def test_direction_change_penalty_extends_cooldown():
    # 直前が値下げ(down)、今回は値上げ(up)方向 → クールダウンが2倍(14日)に伸びる。
    p = _product(current_price=1980, base_price=2200, cost=800)
    orders = [Order("X", AS_OF - timedelta(days=d)) for d in (0, 0, 0, 2, 2, 2)]  # 急売れ
    # 10日前に値下げ:通常7日ならクールダウン明けだが、逆方向ペナルティで14日に延長
    changes = [PriceChange("X", AS_OF - timedelta(days=10), "down", 2100, 1980)]
    ctx = _ctx(orders_by_code={"X": orders}, changes_by_code={"X": changes})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "skip"
    assert "クールダウン" in c.reason


# ---- 値上げ --------------------------------------------------------------- #
def test_markup_on_spike():
    p = _product(current_price=1980, base_price=2200, cost=800)
    orders = [Order("X", AS_OF - timedelta(days=d)) for d in (0, 0, 0, 2, 2, 2)]
    ctx = _ctx(orders_by_code={"X": orders})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "markup"
    assert c.proposed_price is not None and c.proposed_price > p.current_price


def test_event_days_excluded_from_spike():
    # 急売れが全てイベント日なら値上げ判定に使われず、シグナルなしになる。
    p = _product(current_price=1980, base_price=2200, cost=800)
    spike_days = {AS_OF, AS_OF - timedelta(days=1), AS_OF - timedelta(days=2)}
    orders = [Order("X", d) for d in spike_days for _ in range(3)]
    ctx = _ctx(orders_by_code={"X": orders}, event_days=spike_days)
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action != "markup"


def test_markup_weekly_limit_blocks():
    p = _product(current_price=1980, base_price=2200, cost=800)
    orders = [Order("X", AS_OF - timedelta(days=d)) for d in (0, 0, 0, 2, 2, 2)]
    changes = [PriceChange("X", AS_OF - timedelta(days=2), "up", 1920, 1980)]  # 今週すでに値上げ
    ctx = _ctx(orders_by_code={"X": orders}, changes_by_code={"X": changes})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "skip"
    assert "週次上限" in c.reason


def test_markup_needs_approval_when_far_below_base():
    # 定価5000から大きく値引き中(current3000)→ 値上げしても基準比 >20% 乖離。
    p = _product(current_price=3000, base_price=5000, cost=1200)
    orders = [Order("X", AS_OF - timedelta(days=d)) for d in (0, 0, 0, 2, 2, 2)]
    ctx = _ctx(orders_by_code={"X": orders})
    c = evaluate_product(p, GroupConfig(), ctx)
    assert c.action == "needs_approval"


# ---- 端 -------------------------------------------------------------------- #
def test_dummy_dataset_covers_every_action():
    products, ctx = make_dummy_dataset(AS_OF)
    result = {c.code: c.action for c in evaluate_all(products, ctx)}
    assert result == {
        "P-DOWN": "markdown",
        "P-FLOOR": "floor_reached",
        "P-UP": "markup",
        "P-APPUP": "needs_approval",
        "P-COOL": "skip",
        "P-NEW": "skip",
        "P-EXCL": "skip",
        "P-LOSS": "warn",
        "P-REF": "warn",
        "P-STAY": "skip",
        "P-STOCK": "skip",
    }


def test_evaluate_all_uses_group_config():
    # low_frequency グループは値下げ判定窓(N)が45日。14日分の在庫切れでは保留にならない設定確認。
    p = _product(code="G", group="low_frequency", current_price=1980,
                 base_price=2200, cost=900, shipping_cost=200)
    ctx = build_context(AS_OF, orders=[], stockouts=[], changes=[])
    (c,) = evaluate_all([p], ctx)
    assert c.action == "markdown"  # 45日売れず
