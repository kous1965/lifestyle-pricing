"""ドメインモデル(データの入れ物)。

レポートのみモードは以下を入力に取る:
  - Product     : 商品マスタ(現在価格・基準価格・原価・在庫・除外フラグ)
  - Order       : 有効注文(キャンセルは valid=False)
  - StockOutDay : 在庫0だった日(「売れない日数」から除外するため)
  - PriceChange : 価格変更履歴(クールダウン・方向転換の判定に使う)
  - event_days  : Yahooイベント日(値上げ判定から除外する集合。date の set)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Product:
    code: str                       # 商品コード(判定単位。仕様書2章より実質これが現実的)
    current_price: int              # 現在の売価
    base_price: int                 # 基準価格(定価・メーカー希望価格)= 値上げ上限
    registered_on: date             # 登録日(新商品保護期間の起点)
    stock: int                      # 現在在庫数
    cost: int                       # 仕入原価
    shipping_cost: int = 0          # 送料負担額(送料込み商品は必ず入れる ← 送料負け事故対策)
    group: str = "default"          # 判定グループ

    # 除外フラグ(仕様書6.5)。1つでも True なら自動変更の対象外。
    is_set_or_lucky_bag: bool = False       # セット商品・福袋
    is_maker_fixed_price: bool = False      # メーカー指定価格(値下げ禁止)
    is_preorder: bool = False               # 予約・受注生産
    is_sale_price_active: bool = False      # セール価格設定中
    has_reference_price_display: bool = False  # 二重価格(参考価格・OFF率)表示中 → 景表法リスク
    season_paused: bool = False             # 季節商品の判定停止フラグ
    manual_override: bool = False           # 手動変更検知で一時停止中(仕様書6.2)

    def exclusion_reasons(self) -> list[str]:
        """対象外の理由を列挙(空なら対象)。"""
        reasons: list[str] = []
        if self.is_set_or_lucky_bag:
            reasons.append("セット/福袋")
        if self.is_maker_fixed_price:
            reasons.append("メーカー指定価格")
        if self.is_preorder:
            reasons.append("予約/受注生産")
        if self.is_sale_price_active:
            reasons.append("セール価格設定中")
        if self.season_paused:
            reasons.append("季節フラグで判定停止中")
        if self.manual_override:
            reasons.append("手動変更検知で一時停止中")
        return reasons


@dataclass
class Order:
    code: str
    ordered_on: date
    quantity: int = 1
    valid: bool = True              # キャンセル注文は False(仕様書3.3 / 有効注文で再計算)


@dataclass(frozen=True)
class StockOutDay:
    """ある商品がその日、在庫0(＝販売不能)だったことを示す。"""

    code: str
    day: date


@dataclass
class PriceChange:
    code: str
    changed_on: date
    direction: str                  # "down"(値下げ) | "up"(値上げ) | "manual"
    before: int
    after: int


@dataclass
class Candidate:
    """判定結果の1件。レポートはこの一覧を出力する。"""

    code: str
    action: str                     # markdown / markup / floor_reached / needs_approval / warn / skip
    current_price: int
    proposed_price: int | None      # 提案価格(warn/skip では None)
    reason: str                     # 人間向けの一言理由
    details: dict = field(default_factory=dict)  # 判定根拠(注文件数・ベースライン等)

    # 表示用のアクション日本語ラベル
    ACTION_LABELS = {
        "markdown": "値下げ候補",
        "markup": "値上げ候補",
        "floor_reached": "下限到達",
        "needs_approval": "承認待ち(累計変動上限超)",
        "warn": "警告",
        "skip": "対象外/保留",
    }

    @property
    def action_label(self) -> str:
        return self.ACTION_LABELS.get(self.action, self.action)
