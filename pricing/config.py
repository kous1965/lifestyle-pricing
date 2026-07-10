"""判定パラメータ(仕様書7章)。商品グループごとに上書きできる。

数値はすべて仕様書の「例」を初期値にしている。運用開始前に
クライアントと詰めて調整する前提の“たたき台”。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GroupConfig:
    """商品グループ単位の判定設定。"""

    name: str = "default"

    # ---- 値下げ(仕様書3章) ----
    markdown_lookback_days: int = 14        # N: この日数、有効注文が閾値以下なら値下げ候補
    markdown_order_threshold: int = 0       # 「売れない」とみなす注文件数の上限(0 = 1件も無ければ)
    markdown_min_sellable_ratio: float = 0.5  # 直近N日のうち販売可能日がこの割合未満なら判定保留(在庫切れ多)
    markdown_rate: float = 0.03             # X%: 率で下げる場合
    markdown_fixed_yen: int | None = None   # Y円: 固定額で下げる場合(指定時は率より優先)

    # ---- 値上げ(仕様書4章) ----
    markup_lookback_days: int = 3           # P: 直近この日数の急売れを見る
    markup_baseline_multiple: float = 3.0   # K: ベースライン比の倍率
    markup_absolute_min_orders: int = 5     # 併用する絶対件数(普段0.1件/日の誤判定を防ぐ)
    baseline_window_days: int = 60          # ベースライン(日次平均)算出窓(30〜90)
    markup_rate: float = 0.03               # Z%: 値上げ幅
    markup_max_per_week: int = 1            # 1商品あたり週何回まで値上げ可

    # ---- ハンチング防止・共通ガード(仕様書4.3 / 7章) ----
    cooldown_days: int = 7                  # C: 価格変更後この日数は再判定しない
    direction_change_penalty: float = 2.0   # 直前と逆方向ならクールダウンを何倍にするか
    cumulative_cap_rate: float = 0.20       # 基準価格 ±20% を超える変更は自動せず承認待ち
    new_product_protection_days: int = 30   # M: 登録からこの日数は判定対象外

    # ---- 下限価格の料率(仕様書3.2 / 最悪ケース=キャンペーン重畳時) ----
    target_min_margin_rate: float = 0.10    # 目標最低利益率
    yahoo_fee_rate: float = 0.10            # Yahoo手数料率(決済・システム利用料の合算)
    point_fund_rate: float = 0.06           # ポイント原資率(キャンペーン重畳の最悪ケース)
    # ※送料負担額は商品ごとに違うので Product.shipping_cost 側で持つ

    # ---- 端数処理(仕様書6.4) ----
    rounding_mode: str = "x980"             # "x980" | "floor10" | "none"


# グループ名 -> 設定 の対応表。CSV等から動的に組み立ててもよいが、
# まずはコード内デフォルトを1つ持っておく。
DEFAULT_GROUPS: dict[str, GroupConfig] = {
    "default": GroupConfig(),
    # 例) 低単価・低頻度は N を長く(仕様書3.3)
    "low_frequency": GroupConfig(
        name="low_frequency",
        markdown_lookback_days=45,
    ),
}


def get_group_config(group: str, groups: dict[str, GroupConfig] | None = None) -> GroupConfig:
    """グループ名から設定を引く。未定義なら default にフォールバック。"""
    table = groups or DEFAULT_GROUPS
    return table.get(group, table["default"])
