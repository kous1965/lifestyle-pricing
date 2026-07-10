"""レポート出力(report)のテスト。"""

from __future__ import annotations

import csv
import io
from datetime import date

from pricing.data_loader import make_dummy_dataset
from pricing.models import Candidate
from pricing.report import DISPLAY_ORDER, render_csv, render_text, summarize
from pricing.rules import evaluate_all

AS_OF = date(2026, 7, 10)


def _candidates():
    products, ctx = make_dummy_dataset(AS_OF)
    return evaluate_all(products, ctx)


def test_summarize_counts_all_actions():
    counts = summarize(_candidates())
    assert set(DISPLAY_ORDER).issubset(counts)
    assert counts["markdown"] == 1
    assert counts["markup"] == 1
    assert counts["floor_reached"] == 1
    assert counts["needs_approval"] == 1
    assert counts["warn"] == 2
    assert counts["skip"] == 5
    assert sum(counts.values()) == 11


def test_render_text_has_sections_and_total():
    text = render_text(_candidates(), AS_OF)
    assert "基準日 2026-07-10" in text
    assert "合計 11件" in text
    assert "値下げ候補" in text
    assert "承認待ち" in text
    # 提案があるものは増減表記(→)入り、無いものは現在価格のみ
    assert "→" in text
    assert text.endswith("\n")


def test_render_csv_is_parseable():
    csv_text = render_csv(_candidates())
    rows = list(csv.reader(io.StringIO(csv_text)))
    header, *body = rows
    assert header == ["商品コード", "アクション", "現在価格", "提案価格", "増減円", "増減率(%)", "理由"]
    assert len(body) == 11
    by_code = {r[0]: r for r in body}
    # markdown は提案価格・増減が埋まる
    assert by_code["P-DOWN"][3] == "1880"
    assert by_code["P-DOWN"][4] == "-100"
    # warn は提案価格が空
    assert by_code["P-LOSS"][3] == ""


def test_diff_text_handles_missing_proposal():
    # proposed_price None のとき render_text/csv が落ちないこと
    c = Candidate("Z", "warn", 1000, None, "テスト警告")
    assert "テスト警告" in render_text([c])
    assert "Z" in render_csv([c])
