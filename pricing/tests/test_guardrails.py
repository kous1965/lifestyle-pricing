"""guardrails(下限・上限・端数・累計上限)の単体テスト。"""

from __future__ import annotations

import math
from datetime import date

import pytest

from pricing.config import GroupConfig
from pricing.guardrails import (
    ceiling_price,
    clamp_markdown,
    clamp_markup,
    exceeds_cumulative_cap,
    floor_price,
    round_price,
)
from pricing.models import Product


def _product(**kw) -> Product:
    base = dict(code="X", current_price=1000, base_price=1200,
                registered_on=date(2025, 1, 1), stock=5, cost=500)
    base.update(kw)
    return Product(**base)


# ---- floor_price ---------------------------------------------------------- #
def test_floor_price_cost_based():
    cfg = GroupConfig()  # denom = 1 - 0.1 - 0.1 - 0.06 = 0.74
    p = _product(cost=900, shipping_cost=200)
    assert floor_price(p, cfg) == math.ceil((900 + 200) / 0.74)  # 1487


def test_floor_price_takes_higher_of_absolute():
    cfg = GroupConfig()
    p = _product(cost=100, shipping_cost=0)  # 原価ベースは低い
    # 絶対下限の方が高ければそちらが採用される
    assert floor_price(p, cfg, absolute_floor=800) == 800
    # 原価ベースの方が高ければそちら
    p2 = _product(cost=900, shipping_cost=200)
    assert floor_price(p2, cfg, absolute_floor=800) == math.ceil(1100 / 0.74)


def test_floor_price_raises_on_bad_denominator():
    cfg = GroupConfig(target_min_margin_rate=0.5, yahoo_fee_rate=0.4, point_fund_rate=0.2)
    with pytest.raises(ValueError):
        floor_price(_product(), cfg)


def test_ceiling_price_is_base_price():
    assert ceiling_price(_product(base_price=2500)) == 2500


def test_ceiling_price_takes_lower_of_absolute():
    # 絶対上限の方が定価より低ければそちらが採用される(より厳しい方)
    assert ceiling_price(_product(base_price=2500), absolute_ceiling=2000) == 2000
    # 絶対上限の方が定価より高ければ定価のまま(定価より高く売ることはない)
    assert ceiling_price(_product(base_price=2500), absolute_ceiling=3000) == 2500


# ---- round_price ---------------------------------------------------------- #
@pytest.mark.parametrize(
    "price,direction,expected",
    [
        (1600, "down", 1580),   # 末尾80へ切り下げ(仕様書の例)
        (1552, "down", 1480),
        (1480, "down", 1480),   # すでに末尾80ならそのまま
        (2039, "up", 2080),     # 値上げは末尾80へ切り上げ
        (3090, "up", 3180),
        (1480, "up", 1480),
        (50, "down", 50),       # 80円未満は素通し
    ],
)
def test_round_price_x980(price, direction, expected):
    assert round_price(price, "x980", direction=direction) == expected


def test_round_price_up_never_below_input():
    for p in range(80, 5000, 7):
        assert round_price(p, "x980", direction="up") >= p


def test_round_price_down_never_above_input():
    for p in range(80, 5000, 7):
        assert round_price(p, "x980", direction="down") <= p


def test_round_price_floor10_and_none():
    assert round_price(1237, "floor10") == 1230
    assert round_price(1237, "none") == 1237


def test_round_price_unknown_mode():
    with pytest.raises(ValueError):
        round_price(1000, "???")


# ---- clamps --------------------------------------------------------------- #
def test_clamp_markdown():
    assert clamp_markdown(1200, 1000) == (1200, False)
    assert clamp_markdown(900, 1000) == (1000, True)
    assert clamp_markdown(1000, 1000) == (1000, True)


def test_clamp_markup():
    assert clamp_markup(1200, 1500) == (1200, False)
    assert clamp_markup(1600, 1500) == (1500, True)
    assert clamp_markup(1500, 1500) == (1500, True)


# ---- cumulative cap ------------------------------------------------------- #
def test_exceeds_cumulative_cap():
    cfg = GroupConfig()  # cap 0.20
    assert exceeds_cumulative_cap(760, 1000, cfg) is True    # -24%
    assert exceeds_cumulative_cap(850, 1000, cfg) is False   # -15%
    assert exceeds_cumulative_cap(1000, 1000, cfg) is False
    # 基準価格0以下は常に承認待ち(0除算回避)
    assert exceeds_cumulative_cap(500, 0, cfg) is True
