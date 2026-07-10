"""商品ごとの最低売価・最高売価(絶対下限/絶対上限)の永続ストア。

本番ロジック側にはもともと商品単位の絶対下限(absolute_floor)・絶対上限
(absolute_ceiling)を指定できる仕組みがある(guardrails.floor_price /
ceiling_price)。デモ側では、それを画面から設定できるように:

- `pricing/demo/data/product_bounds.json` に {商品コード: {floor, ceiling}} で保存
  (client_products.json と同様、.gitignore 済みでリポジトリには含めない)
- 日常の微調整は画面上での1件ずつの編集(app.py の st.data_editor)
- 新商品を多数追加するときなどはCSV一括インポート(merge_csv)

の両方からこのストアを更新する想定。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "product_bounds.json"

Bounds = dict[str, dict[str, "int | None"]]

CSV_COLUMNS = ["商品コード", "最低売価", "最高売価"]


def load(path: Path = DATA_PATH) -> Bounds:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {code: {"floor": v.get("floor"), "ceiling": v.get("ceiling")} for code, v in raw.items()}


def save(bounds: Bounds, path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # floor・ceiling とも未設定のエントリは保存しない(ファイルを肥大させない)
    cleaned = {
        code: v for code, v in bounds.items()
        if v.get("floor") is not None or v.get("ceiling") is not None
    }
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def set_bound(bounds: Bounds, code: str, floor: int | None, ceiling: int | None) -> Bounds:
    """1商品分の値を差し替えた新しい辞書を返す(元の bounds は変更しない)。"""
    updated = dict(bounds)
    if floor is None and ceiling is None:
        updated.pop(code, None)
    else:
        updated[code] = {"floor": floor, "ceiling": ceiling}
    return updated


def merge_csv(bounds: Bounds, csv_text: str) -> tuple[Bounds, list[str]]:
    """CSV(商品コード,最低売価,最高売価)を取り込んでマージする。

    戻り値: (更新後の辞書, 警告メッセージ一覧)。値が空欄の列はその項目を
    未設定のままにする(既存の設定を消したい場合は 0 ではなく空欄のままにせず、
    別途1件ずつの編集で消す)。
    """
    updated = dict(bounds)
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None or "商品コード" not in reader.fieldnames:
        warnings.append("CSVのヘッダーに「商品コード」列が見つかりません。")
        return bounds, warnings

    for i, row in enumerate(reader, start=2):  # 1行目はヘッダー
        code = (row.get("商品コード") or "").strip()
        if not code:
            continue

        def _parse(col: str) -> int | None:
            raw = (row.get(col) or "").strip()
            if not raw:
                return None
            try:
                return int(raw)
            except ValueError:
                warnings.append(f"{i}行目: 「{col}」の値「{raw}」を数値として読めませんでした")
                return None

        floor = _parse("最低売価")
        ceiling = _parse("最高売価")
        existing = updated.get(code, {})
        merged_floor = floor if floor is not None else existing.get("floor")
        merged_ceiling = ceiling if ceiling is not None else existing.get("ceiling")
        if merged_floor is None and merged_ceiling is None:
            continue
        updated[code] = {"floor": merged_floor, "ceiling": merged_ceiling}

    return updated, warnings


def to_absolute_dicts(bounds: Bounds) -> tuple[dict[str, int], dict[str, int]]:
    """build_context() にそのまま渡せる (absolute_floors, absolute_ceilings) の形に変換。"""
    floors = {code: v["floor"] for code, v in bounds.items() if v.get("floor") is not None}
    ceilings = {code: v["ceiling"] for code, v in bounds.items() if v.get("ceiling") is not None}
    return floors, ceilings


def export_csv(bounds: Bounds, codes: list[str], name_by_code: dict[str, str]) -> str:
    """現在の商品一覧 + 既存設定を、編集用CSVとして書き出す(ダウンロード用)。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["商品コード", "商品名", "最低売価", "最高売価"])
    for code in codes:
        v = bounds.get(code, {})
        writer.writerow([code, name_by_code.get(code, ""), v.get("floor") or "", v.get("ceiling") or ""])
    return buf.getvalue()
