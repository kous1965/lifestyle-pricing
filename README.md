# lifestyle-pricing

Yahoo!ショッピング向けの擬似ダイナミックプライシング。
仕様書 v0.1 の「レポートのみモード(6.1)」を実装。価格の自動書き込みは行わず、
値下げ候補・値上げ候補・下限到達・承認待ち・警告を算出して出力する。

（元は `lifestyle-image-check` リポジトリに同居していたものを独立リポジトリに分離。
画像チェック側のコードはそちらに残っている。）

## 構成

```
pricing/
  config.py       判定パラメータ(仕様書7章)。商品グループごとに上書き可
  models.py       ドメインモデル(Product / Order / StockOutDay / PriceChange / Candidate)
  guardrails.py   下限・上限・端数処理・累計変動上限
  rules.py        判定ロジック本体(値下げ/値上げ/ハンチング防止)
  report.py       レポート出力(テキスト / CSV)
  data_loader.py  入力整形 + 動作確認用ダミーデータ
  tests/          pytest 一式
  demo/           クライアント提示用のデモ操作パネル(Streamlit)。本番機能ではない
```

## 使い方

```bash
# 依存インストール
pip install -r pricing/requirements.txt

# ダミーデータでレポート出力(動作確認)
python -m pricing.data_loader

# テスト
python -m pytest pricing/tests -q
```

## コントロールパネル(デモ版)

クライアントに触ってもらうための Streamlit 製デモ。仕様書5章(Yahoo注文API連携)が
未着手のため、実際の注文データは使えない。代わりにクライアント店舗の公開商品ページ
(`https://store.shopping.yahoo.co.jp/lifestyle-007/`)から商品名・現売価だけを
スナップショット取得し、判定ロジックを動かすための合成の売れ行きデータ(注文・在庫切れ・
過去の価格変更)を重ねて見せている。「シナリオ」列にどの分岐を演じさせているかが出るので、
実売れ行きと混同しないようにしている。

```bash
# 1) 実商品名・実売価のスナップショットを取得(初回 or 更新したい時のみ。継続実行はしない)
python -m pricing.demo.scrape_client_products

# 2) デモパネルを起動
streamlit run pricing/demo/app.py
```

サイドバーで仕様書7章の判定パラメータ(値下げ率・クールダウン日数など)や合成データの
乱数シードをその場で変更でき、判定結果・テキスト/CSVレポートが即座に再計算される。

画面構成・操作方法は [pricing/demo/MANUAL.md](pricing/demo/MANUAL.md) を参照。

## 環境変数

`cp .env.example .env` して実値を入れる（`.env` は gitignore 済み）。
Yahoo API 認証情報と実行モード（既定 `report_only`）を持つ。

## 開発コンテナ

`.devcontainer/` に Python 3.13 + Node.js + Claude Code CLI 入りの Dev Container を同梱。
VSCode で「Dev Containers: Reopen in Container」から利用する。隔離環境なので、
コンテナ内では `claude --dangerously-skip-permissions` でも安全に回せる。
