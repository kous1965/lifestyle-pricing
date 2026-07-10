"""クライアント店舗(ライフスタイルYahoo!店)の商品名・現売価スナップショット取得。

対象: https://store.shopping.yahoo.co.jp/lifestyle-007/search.html
- ログイン不要の公開商品一覧ページ。robots.txt でも当該パスは禁止されていない。
- 取れるのは商品名・現売価・画像URLなど「今見えているもの」だけで、
  注文履歴や在庫切れ日といった判定ロジックに必要な販売実績は取れない
  (それは合成データで別途 synthetic_data.py が補う)。
- あくまでデモ用の一時的なスナップショット取得。継続的な自動取得や
  再配布はしない前提(取得結果はこのファイルの実行者のローカルにのみ保存し、
  pricing/demo/data/ は .gitignore 済み)。

使い方:
    python -m pricing.demo.scrape_client_products
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

STORE_URL = "https://store.shopping.yahoo.co.jp/lifestyle-007/search.html"
DATA_PATH = Path(__file__).parent / "data" / "client_products.json"
USER_AGENT = "Mozilla/5.0 (compatible; lifestyle-pricing-demo/1.0)"
PAGE_SIZE = 30  # ページあたり件数(サイト側の既定)
REQUEST_DELAY_SEC = 1.0  # 相手サーバへの配慮。連続リクエストの間隔


@dataclass
class ScrapedProduct:
    item_id: str
    name: str
    list_price: int      # price(定価/参考価格側。仕様書の base_price に相当)
    actual_price: int    # actualPrice(現在の実売価)
    url: str
    image_url: str | None
    has_no_stock: bool


def _find_result_items(node: object) -> list[dict] | None:
    """__NEXT_DATA__ の中から type=='RESULT' の商品一覧ブロックを探す。

    Yahoo!ショッピング側のJSON構造(bff配下のキー)は変わりうるため、
    深さ優先で総当たりする。
    """
    if isinstance(node, dict):
        if node.get("type") == "RESULT":
            items = node.get("content", {}).get("items")
            if isinstance(items, list):
                return items
        for v in node.values():
            found = _find_result_items(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_result_items(v)
            if found is not None:
                return found
    return None


def fetch_page(offset: int, session: requests.Session) -> list[dict]:
    resp = session.get(
        STORE_URL,
        params={"b": offset, "view": "grid"},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()
    marker = 'id="__NEXT_DATA__"'
    start = resp.text.index(marker)
    start = resp.text.index(">", start) + 1
    end = resp.text.index("</script>", start)
    next_data = json.loads(resp.text[start:end])
    items = _find_result_items(next_data)
    return items or []


def scrape(max_items: int = 60) -> list[ScrapedProduct]:
    """商品を最大 max_items 件、公開一覧ページから取得する。"""
    products: list[ScrapedProduct] = []
    session = requests.Session()
    offset = 1
    while len(products) < max_items:
        raw_items = fetch_page(offset, session)
        if not raw_items:
            break
        for item in raw_items:
            if len(products) >= max_items:
                break
            products.append(
                ScrapedProduct(
                    item_id=item["itemId"],
                    name=item["name"],
                    list_price=int(item.get("price") or item.get("actualPrice") or 0),
                    actual_price=int(item.get("actualPrice") or item.get("price") or 0),
                    url=item.get("url", ""),
                    image_url=(item.get("image") or {}).get("imageUrl"),
                    has_no_stock=bool((item.get("labels") or {}).get("hasNoStock", False)),
                )
            )
        offset += PAGE_SIZE
        if len(products) < max_items:
            time.sleep(REQUEST_DELAY_SEC)
    return products


def save(products: list[ScrapedProduct], path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load(path: Path = DATA_PATH) -> list[ScrapedProduct]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ScrapedProduct(**r) for r in raw]


def _main() -> None:
    products = scrape()
    save(products)
    print(f"{len(products)}件を取得 → {DATA_PATH}")


if __name__ == "__main__":
    _main()
