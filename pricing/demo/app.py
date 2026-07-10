"""デモ用コントロールパネル(Streamlit)。

実商品名・実売価(クライアント店舗からのスナップショット) + 合成の売れ行き
データを、レポートのみモードの判定エンジンにそのまま通し、判定パラメータ
(仕様書7章)をその場で動かしながら結果がどう変わるかを見せる。

起動:
    streamlit run pricing/demo/app.py
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

import streamlit as st

# `streamlit run pricing/demo/app.py` はこのファイルを単独スクリプトとして
# 実行するため、相対importではなくリポジトリルートを sys.path に足した上で
# 絶対importする必要がある。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pricing.config import GroupConfig
from pricing.report import render_csv, render_text, summarize
from pricing.rules import evaluate_all
from pricing.demo.scrape_client_products import DATA_PATH, ScrapedProduct, load, save, scrape
from pricing.demo.synthetic_data import SCENARIO_LABELS, build_synthetic_dataset

st.set_page_config(page_title="擬似ダイナミックプライシング デモ", layout="wide")


@st.cache_data(show_spinner=False)
def _load_snapshot(mtime: float) -> list[ScrapedProduct]:
    """mtime をキャッシュキーにして、スナップショットが更新されたら読み直す。"""
    return load()


def _snapshot_products() -> list[ScrapedProduct] | None:
    if not DATA_PATH.exists():
        return None
    return _load_snapshot(DATA_PATH.stat().st_mtime)


def _sidebar_config() -> tuple[GroupConfig, date, int]:
    st.sidebar.header("判定パラメータ(仕様書7章)")
    st.sidebar.caption("動かすと下の判定結果がその場で再計算されます。")

    as_of = st.sidebar.date_input("基準日", value=date.today())

    st.sidebar.subheader("値下げ(3章)")
    markdown_lookback_days = st.sidebar.slider("値下げ判定の参照日数(N)", 7, 60, 14)
    markdown_rate = st.sidebar.slider("値下げ率(X%)", 0.01, 0.15, 0.03, step=0.01)

    st.sidebar.subheader("値上げ(4章)")
    markup_lookback_days = st.sidebar.slider("急売れ判定の直近日数(P)", 1, 14, 3)
    markup_baseline_multiple = st.sidebar.slider("ベースライン倍率(K)", 1.5, 6.0, 3.0, step=0.5)
    markup_rate = st.sidebar.slider("値上げ率(Z%)", 0.01, 0.15, 0.03, step=0.01)

    st.sidebar.subheader("ハンチング防止・共通ガード(4.3 / 7章)")
    cooldown_days = st.sidebar.slider("クールダウン日数(C)", 1, 30, 7)
    cumulative_cap_rate = st.sidebar.slider("累計変動上限(基準価格比)", 0.05, 0.40, 0.20, step=0.05)
    new_product_protection_days = st.sidebar.slider("新商品保護期間(M・日)", 0, 60, 30)

    st.sidebar.subheader("端数処理(6.4)")
    rounding_mode = st.sidebar.selectbox("丸めモード", ["x980", "floor10", "none"], index=0)

    st.sidebar.divider()
    seed = st.sidebar.number_input("合成データの乱数シード", min_value=0, max_value=9999, value=1, step=1)
    st.sidebar.caption("シードを変えると、同じ実商品に別のシナリオ・売れ行きパターンを割り当て直します。")

    cfg = dataclasses.replace(
        GroupConfig(),
        markdown_lookback_days=markdown_lookback_days,
        markdown_rate=markdown_rate,
        markup_lookback_days=markup_lookback_days,
        markup_baseline_multiple=markup_baseline_multiple,
        markup_rate=markup_rate,
        cooldown_days=cooldown_days,
        cumulative_cap_rate=cumulative_cap_rate,
        new_product_protection_days=new_product_protection_days,
        rounding_mode=rounding_mode,
    )
    return cfg, as_of, int(seed)


def _snapshot_controls() -> None:
    st.sidebar.divider()
    st.sidebar.subheader("実商品データ")
    if DATA_PATH.exists():
        import datetime as _dt

        mtime = _dt.datetime.fromtimestamp(DATA_PATH.stat().st_mtime)
        st.sidebar.caption(f"スナップショット取得日時: {mtime:%Y-%m-%d %H:%M}")
    else:
        st.sidebar.caption("スナップショット未取得です。下のボタンで取得してください。")

    if st.sidebar.button("クライアント店舗から実商品名・実売価を再取得(60件)"):
        with st.sidebar.status("取得中…", expanded=False):
            products = scrape(max_items=60)
            save(products)
        st.cache_data.clear()
        st.rerun()


def main() -> None:
    st.title("擬似ダイナミックプライシング — コントロールパネル(デモ版)")
    st.info(
        "**これはデモです。** 商品名・現売価はクライアント店舗の公開ページから取得した実データですが、"
        "注文履歴・在庫切れ日といった売れ行きは仕様書5章(Yahoo注文API連携)が未着手のため、"
        "判定ロジックの各分岐を一通り見せるための**合成データ**です。「シナリオ」列がその合成の意図を示します。",
        icon="ℹ️",
    )

    cfg, as_of, seed = _sidebar_config()
    _snapshot_controls()

    scraped = _snapshot_products()
    if not scraped:
        st.warning(
            "実商品データがまだありません。サイドバー下部の「クライアント店舗から実商品名・実売価を再取得」"
            "を押すか、`python -m pricing.demo.scrape_client_products` を実行してください。"
        )
        return

    products, ctx, code_to_scenario = build_synthetic_dataset(scraped, as_of, seed=seed)
    name_by_code = {sp.item_id: sp.name for sp in scraped}
    url_by_code = {sp.item_id: sp.url for sp in scraped}

    candidates = evaluate_all(products, ctx, groups={"default": cfg})
    counts = summarize(candidates)

    cols = st.columns(6)
    labels = [
        ("値下げ候補", "markdown"),
        ("値上げ候補", "markup"),
        ("下限到達", "floor_reached"),
        ("承認待ち", "needs_approval"),
        ("警告", "warn"),
        ("対象外/据え置き", "skip"),
    ]
    for col, (label, key) in zip(cols, labels):
        col.metric(label, counts[key])

    st.subheader(f"判定結果一覧({len(candidates)}件、基準日 {as_of.isoformat()})")

    rows = []
    for c in candidates:
        rows.append(
            {
                "商品コード": c.code,
                "商品名": name_by_code.get(c.code, ""),
                "シナリオ(合成データ)": SCENARIO_LABELS.get(code_to_scenario.get(c.code, ""), ""),
                "アクション": c.action_label,
                "現在価格": c.current_price,
                "提案価格": c.proposed_price if c.proposed_price is not None else None,
                "理由": c.reason,
                "商品ページ": url_by_code.get(c.code, ""),
            }
        )
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "現在価格": st.column_config.NumberColumn(format="%d円"),
            "提案価格": st.column_config.NumberColumn(format="%d円"),
            "商品ページ": st.column_config.LinkColumn(display_text="開く"),
        },
    )

    with st.expander("テキストレポート(コンソール/メール向け)"):
        text_report = render_text(candidates, as_of)
        st.code(text_report, language=None)
        st.download_button("テキストレポートをダウンロード", text_report, file_name="pricing_report.txt")

    with st.expander("CSVレポート(スプレッドシート貼り付け用)"):
        csv_report = render_csv(candidates)
        st.code(csv_report, language=None)
        st.download_button("CSVをダウンロード", csv_report, file_name="pricing_report.csv")


if __name__ == "__main__":
    main()
