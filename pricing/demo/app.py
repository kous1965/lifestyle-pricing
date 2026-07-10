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

import pandas as pd
import streamlit as st

# `streamlit run pricing/demo/app.py` はこのファイルを単独スクリプトとして
# 実行するため、相対importではなくリポジトリルートを sys.path に足した上で
# 絶対importする必要がある。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pricing.config import GroupConfig
from pricing.report import render_csv, render_text, summarize
from pricing.rules import evaluate_all
from pricing.demo import product_bounds as pb
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
    st.sidebar.header("判定パラメータ")
    st.sidebar.caption("動かすと下の判定結果がその場で再計算されます。")

    as_of = st.sidebar.date_input(
        "基準日",
        value=date.today(),
        help="判定の基準となる「今日」の日付です。通常はそのままで大丈夫です。",
    )

    st.sidebar.subheader("値下げ")
    markdown_lookback_days = st.sidebar.slider(
        "値下げ判定の参照日数",
        7, 60, 14,
        help="商品が「売れていない」と判断するために、過去何日分の注文状況を見るかを"
        "設定します。日数を短くするほど、少し売れ行きが落ちただけでも値下げ候補に"
        "上がりやすくなります。",
    )
    markdown_rate = st.sidebar.slider(
        "値下げ率(%)",
        0.01, 0.15, 0.03, step=0.01,
        help="値下げ候補になった商品を、何%値下げするかを設定します。",
    )

    st.sidebar.subheader("値上げ")
    markup_lookback_days = st.sidebar.slider(
        "急売れ判定の直近日数",
        1, 14, 3,
        help="「急に売れ出した」と判断するために、直近何日分の注文状況を見るかを"
        "設定します。",
    )
    markup_baseline_multiple = st.sidebar.slider(
        "ベースライン倍率",
        1.5, 6.0, 3.0, step=0.5,
        help="普段の売れ行き(平均)と比べて、何倍以上売れたら「急売れ」とみなすかを"
        "設定します。数字が大きいほど、よほど急に売れないと値上げ候補になりません。",
    )
    markup_rate = st.sidebar.slider(
        "値上げ率(%)",
        0.01, 0.15, 0.03, step=0.01,
        help="値上げ候補になった商品を、何%値上げするかを設定します。",
    )

    st.sidebar.subheader("ハンチング防止・共通ガード")
    cooldown_days = st.sidebar.slider(
        "クールダウン日数",
        1, 30, 7,
        help="一度値段を変えた商品を、その後何日間は自動で再判定しないようにするかを"
        "設定します。値段が短期間でコロコロ変わるのを防ぐための「お休み期間」です。",
    )
    cumulative_cap_rate = st.sidebar.slider(
        "累計変動上限(基準価格比)",
        0.05, 0.40, 0.20, step=0.05,
        help="提案する価格が定価からどれくらい離れたら、自動では反映せず"
        "「人の確認(承認)を必要とする」扱いにするかを設定します。",
    )
    new_product_protection_days = st.sidebar.slider(
        "新商品保護期間(日)",
        0, 60, 30,
        help="販売を始めたばかりの新商品を、登録から何日間は自動値付けの対象外に"
        "するかを設定します。",
    )

    st.sidebar.subheader("端数処理")
    rounding_mode = st.sidebar.selectbox(
        "丸めモード",
        ["x980", "floor10", "none"],
        index=0,
        help="提案する価格の端数をどう整えるかを設定します。「x980」は980円のような、"
        "お店でよく見るキリのいい価格に自動で揃えます。「floor10」は10円未満を"
        "切り捨てるだけ、「none」は端数調整をしません。",
    )

    st.sidebar.divider()
    seed = st.sidebar.number_input(
        "合成データの乱数シード",
        min_value=0, max_value=9999, value=1, step=1,
        help="デモ用に、どの商品にどんな売れ行きパターン(値下げ候補・値上げ候補など)を"
        "割り当てるかを決める数字です。変えると組み合わせが変わります(本番の"
        "機能ではありません)。",
    )
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


def _product_bounds_section(scraped: list[ScrapedProduct]) -> tuple[dict[str, int], dict[str, int]]:
    """商品ごとの最低売価・最高売価を、CSV一括取込 + 画面編集の両方で設定できるようにする。

    戻り値: (absolute_floors, absolute_ceilings) — build_synthetic_dataset にそのまま渡す。
    """
    bounds = pb.load()
    codes = [sp.item_id for sp in scraped]
    name_by_code = {sp.item_id: sp.name for sp in scraped}
    price_by_code = {sp.item_id: (sp.actual_price or sp.list_price) for sp in scraped}

    with st.expander("商品ごとの最低売価・最高売価(任意設定)", expanded=False):
        st.caption(
            "自動値下げ/値上げでも、ここで指定した価格の範囲は絶対に超えません"
            "(原価から自動計算される下限・定価の上限より、こちらが優先されます)。"
            "空欄のままなら自動計算のみが使われます。"
        )

        st.markdown("**CSVで一括登録**(新商品を多数追加するときなど)")
        col1, col2 = st.columns(2)
        with col1:
            uploaded = st.file_uploader(
                "CSVをアップロード(商品コード, 最低売価, 最高売価)", type=["csv"], key="bounds_csv"
            )
            if uploaded is not None:
                csv_text = uploaded.getvalue().decode("utf-8-sig")
                bounds, warnings = pb.merge_csv(bounds, csv_text)
                pb.save(bounds)
                st.success("CSVを取り込みました。")
                for w in warnings:
                    st.warning(w)
        with col2:
            st.download_button(
                "現在の設定をCSVでダウンロード(編集用テンプレート)",
                pb.export_csv(bounds, codes, name_by_code),
                file_name="product_bounds.csv",
            )

        st.markdown("**画面で1件ずつ編集**")
        table = pd.DataFrame(
            [
                {
                    "商品コード": code,
                    "商品名": name_by_code.get(code, ""),
                    "現在価格": price_by_code.get(code),
                    "最低売価": bounds.get(code, {}).get("floor"),
                    "最高売価": bounds.get(code, {}).get("ceiling"),
                }
                for code in codes
            ]
        )
        edited = st.data_editor(
            table,
            hide_index=True,
            width="stretch",
            disabled=["商品コード", "商品名", "現在価格"],
            column_config={
                "現在価格": st.column_config.NumberColumn(format="%d円"),
                "最低売価": st.column_config.NumberColumn(
                    format="%d円", min_value=0, help="この価格より下には自動で下げません"
                ),
                "最高売価": st.column_config.NumberColumn(
                    format="%d円", min_value=0, help="この価格より上には自動で上げません"
                ),
            },
            key="bounds_editor",
        )

        new_bounds: dict[str, dict[str, int | None]] = {}
        for _, row in edited.iterrows():
            floor = None if pd.isna(row["最低売価"]) else int(row["最低売価"])
            ceiling = None if pd.isna(row["最高売価"]) else int(row["最高売価"])
            new_bounds = pb.set_bound(new_bounds, row["商品コード"], floor, ceiling)
        if new_bounds != bounds:
            pb.save(new_bounds)
            bounds = new_bounds

    return pb.to_absolute_dicts(bounds)


def main() -> None:
    st.title("擬似ダイナミックプライシング — コントロールパネル(デモ版)")
    st.info(
        "**これはデモです。** 商品名・現売価はクライアント店舗の公開ページから取得した実データですが、"
        "注文履歴・在庫切れ日といった売れ行きはYahoo!ショッピング注文APIとの連携がまだ実装されていないため、"
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

    absolute_floors, absolute_ceilings = _product_bounds_section(scraped)
    products, ctx, code_to_scenario = build_synthetic_dataset(
        scraped, as_of, seed=seed,
        absolute_floors=absolute_floors, absolute_ceilings=absolute_ceilings,
    )
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
