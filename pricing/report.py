"""レポート出力(仕様書6.1 レポートのみモード)。

Candidate の一覧を人間が読める形にまとめる。価格は書き込まず、
値下げ候補・値上げ候補・下限到達・承認待ち・警告を並べるだけ。

- summarize()     : アクション別の件数集計
- render_text()   : コンソール/メール向けのプレーンテキスト
- render_csv()    : スプレッドシート貼り付け用CSV(承認担当がそのまま使える)
"""

from __future__ import annotations

import csv
import io
from datetime import date

from .models import Candidate

# レポートに並べる順番(重要度が高い=人が見るべき順)。
DISPLAY_ORDER = ["needs_approval", "warn", "markdown", "markup", "floor_reached", "skip"]


def summarize(candidates: list[Candidate]) -> dict[str, int]:
    """アクション別の件数を数える(0件のアクションも含めて返す)。"""
    counts = {action: 0 for action in DISPLAY_ORDER}
    for c in candidates:
        counts[c.action] = counts.get(c.action, 0) + 1
    return counts


def _diff_text(c: Candidate) -> str:
    """現在価格 → 提案価格 の増減を「-120円 / -3.4%」のように整形。"""
    if c.proposed_price is None or c.current_price <= 0:
        return "—"
    diff = c.proposed_price - c.current_price
    pct = diff / c.current_price * 100
    sign = "+" if diff > 0 else ""
    return f"{c.current_price:,} → {c.proposed_price:,}円({sign}{diff:,}円 / {sign}{pct:.1f}%)"


def render_text(candidates: list[Candidate], as_of: date | None = None) -> str:
    """コンソール/メール向けのテキストレポートを組み立てる。"""
    lines: list[str] = []
    header = "擬似ダイナミックプライシング レポート(レポートのみモード)"
    if as_of is not None:
        header += f" — 基準日 {as_of.isoformat()}"
    lines.append(header)
    lines.append("=" * len(header))

    counts = summarize(candidates)
    total = len(candidates)
    summary_bits = [
        f"値下げ候補 {counts['markdown']}",
        f"値上げ候補 {counts['markup']}",
        f"下限到達 {counts['floor_reached']}",
        f"承認待ち {counts['needs_approval']}",
        f"警告 {counts['warn']}",
        f"対象外/据え置き {counts['skip']}",
    ]
    lines.append(f"合計 {total}件 — " + " / ".join(summary_bits))
    lines.append("")

    # skip(対象外/据え置き)は件数が多くなりがちなので末尾にまとめる。
    by_action: dict[str, list[Candidate]] = {a: [] for a in DISPLAY_ORDER}
    for c in candidates:
        by_action.setdefault(c.action, []).append(c)

    for action in DISPLAY_ORDER:
        items = by_action.get(action, [])
        if not items:
            continue
        label = Candidate.ACTION_LABELS.get(action, action)
        lines.append(f"■ {label}({len(items)}件)")
        for c in items:
            if action in ("markdown", "markup", "floor_reached", "needs_approval"):
                lines.append(f"  - {c.code}: {_diff_text(c)}  … {c.reason}")
            else:
                lines.append(f"  - {c.code}: {c.current_price:,}円  … {c.reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_csv(candidates: list[Candidate]) -> str:
    """スプレッドシート貼り付け用CSV。承認担当がそのまま確認・転記できる列構成。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["商品コード", "アクション", "現在価格", "提案価格", "増減円", "増減率(%)", "理由"]
    )
    for c in candidates:
        if c.proposed_price is not None:
            diff = c.proposed_price - c.current_price
            pct = (diff / c.current_price * 100) if c.current_price else 0.0
            writer.writerow(
                [c.code, c.action_label, c.current_price, c.proposed_price, diff, f"{pct:.1f}", c.reason]
            )
        else:
            writer.writerow([c.code, c.action_label, c.current_price, "", "", "", c.reason])
    return buf.getvalue()
