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

## 環境変数

`cp .env.example .env` して実値を入れる（`.env` は gitignore 済み）。
Yahoo API 認証情報と実行モード（既定 `report_only`）を持つ。

## 開発コンテナ

`.devcontainer/` に Python 3.13 + Node.js + Claude Code CLI 入りの Dev Container を同梱。
VSCode で「Dev Containers: Reopen in Container」から利用する。隔離環境なので、
コンテナ内では `claude --dangerously-skip-permissions` でも安全に回せる。
