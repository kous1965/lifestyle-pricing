"""ガードレール(仕様書3.2 / 4.1 / 6.4 / 4.3)。

- 下限価格 : 原価ベース下限 と 絶対下限単価 の高い方
- 上限価格 : 基準価格(定価)
- 端数処理 : 心理価格丸め
- 累計変動上限 : 基準価格 ±X% を超える提案は自動せず承認待ち
"""

from __future__ import annotations

import math

from .config import GroupConfig
from .models import Product


def floor_price(product: Product, cfg: GroupConfig, absolute_floor: int | None = None) -> int:
    """下限価格 = max(原価ベース下限, 絶対下限単価)。

    原価ベース下限(仕様書3.2 の 0.81係数のYahoo料率版):
        (仕入原価 + 送料負担額) / (1 − 目標最低利益率 − Yahoo手数料率 − ポイント原資率)

    ※料率は最悪ケース(キャンペーン重畳時)を使う。分母が0以下になる設定は事故なので例外。
    """
    denom = 1.0 - cfg.target_min_margin_rate - cfg.yahoo_fee_rate - cfg.point_fund_rate
    if denom <= 0:
        raise ValueError(
            f"下限価格の分母が0以下です(料率合計が高すぎる): denom={denom:.3f} "
            f"group={cfg.name}"
        )
    cost_based = (product.cost + product.shipping_cost) / denom
    cost_based_floor = math.ceil(cost_based)  # 下限は切り上げ側で安全に

    if absolute_floor is not None:
        return max(cost_based_floor, absolute_floor)
    return cost_based_floor


def ceiling_price(product: Product, absolute_ceiling: int | None = None) -> int:
    """上限価格 = min(基準価格, 商品ごとの絶対上限単価)(仕様書4.1)。

    絶対上限単価は、下限側の absolute_floor と対になる仕組み。「定価まで
    上げてよいはずだが、この商品だけは○○円より上には絶対にしない」という
    商品単位の指定があれば、そちらが基準価格より優先(=より厳しい方)される。
    """
    if absolute_ceiling is not None:
        return min(product.base_price, absolute_ceiling)
    return product.base_price


def round_price(price: int, mode: str, direction: str = "down") -> int:
    """端数処理(仕様書6.4)。

    direction は丸める向き。値下げは "down"(安い側へ)、値上げは "up"(高い側へ)を渡す。
    向きを取り違えると、値上げなのに現在価格より下に丸められて“上げ余地なし”になる事故を招く。

    - "x980"   : 心理価格。末尾を ◯80 円に揃える(100円刻み。例: 1600 -> 1580, 2039 -↑-> 2080)
    - "floor10": 10円未満切り捨て(向きに関わらず切り捨て)
    - "none"   : そのまま
    """
    if mode == "none":
        return price
    if mode == "floor10":
        return (price // 10) * 10
    if mode == "x980":
        # 末尾80円(…, 1480, 1580, 1680, …)のうち、down なら price 以下で最大、
        # up なら price 以上で最小のものに丸める。80円未満は素通し。
        if price < 80:
            return max(price, 0)
        if direction == "up":
            k = (price - 80 + 99) // 100   # ceil
        else:
            k = (price - 80) // 100         # floor
        return max(k * 100 + 80, 0)
    raise ValueError(f"未知の端数処理モード: {mode}")


def clamp_markdown(proposed: int, floor: int) -> tuple[int, bool]:
    """値下げ提案を下限でクランプ。戻り値: (確定価格, 下限に張り付いたか)。"""
    if proposed <= floor:
        return floor, True
    return proposed, False


def clamp_markup(proposed: int, ceiling: int) -> tuple[int, bool]:
    """値上げ提案を上限でクランプ。戻り値: (確定価格, 上限に張り付いたか)。"""
    if proposed >= ceiling:
        return ceiling, True
    return proposed, False


def exceeds_cumulative_cap(proposed: int, base_price: int, cfg: GroupConfig) -> bool:
    """基準価格からの累計変動が上限(±cap)を超えるか(仕様書4.3-4)。

    超える場合は自動反映せず「承認待ち」に回す。
    """
    if base_price <= 0:
        return True
    deviation = abs(proposed - base_price) / base_price
    return deviation > cfg.cumulative_cap_rate
