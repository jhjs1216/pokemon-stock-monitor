import json
import os
import random
import re
import time
from html import unescape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


PRODUCTS_JSON = Path("products.json")
PRODUCTS_TXT = Path("products.txt")
STATE_FILE = Path(".stock_state.json")
SHOPBY_API_BASE_URL = "https://shop-api.e-ncp.com"

NTFY_TOPIC_URL = os.getenv("NTFY_TOPIC_URL", "").strip()
ALERT_ON_FIRST_IN_STOCK = os.getenv("ALERT_ON_FIRST_IN_STOCK", "false").lower() == "true"

REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
SLEEP_MIN_SECONDS = float(os.getenv("SLEEP_MIN_SECONDS", "2"))
SLEEP_MAX_SECONDS = float(os.getenv("SLEEP_MAX_SECONDS", "5"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 PokemonStoreStockMonitor/1.0"
    ),
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

SOLD_OUT_PATTERNS = [
    r"sold\s*out",
    r"품절",
    r"일시\s*품절",
    r"재고\s*없음",
    r"판매\s*종료",
    r"구매\s*불가",
]

BUYABLE_PATTERNS = [
    r"구매하기",
    r"바로\s*구매",
    r"장바구니",
    r"cart",
    r"buy\s*now",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_products() -> List[str]:
    if PRODUCTS_JSON.exists():
        data = json.loads(PRODUCTS_JSON.read_text(encoding="utf-8"))
        if isinstance(data, list):
            urls = []
            for item in data:
                if isinstance(item, str):
                    urls.append(item.strip())
                elif isinstance(item, dict) and item.get("url"):
                    urls.append(str(item["url"]).strip())
            return [url for url in urls if url]
        raise ValueError("products.json must be a list of URLs or objects with a url field.")

    if PRODUCTS_TXT.exists():
        urls = []
        for line in PRODUCTS_TXT.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
        return urls

    raise FileNotFoundError("products.json or products.txt is required.")


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"products": {}}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("products"), dict):
            return data
    except json.JSONDecodeError:
        pass

    return {"products": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def text_matches_any(text: str, patterns: List[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def extract_product_name(soup: BeautifulSoup) -> str:
    selectors = [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        "h1",
        ".product-name",
        ".prd-name",
        ".goods-name",
        ".name",
        "title",
    ]

    for selector in selectors:
        node = soup.select_one(selector)
        if not node:
            continue

        value = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        if value:
            value = re.sub(r"\s+", " ", value).strip()
            value = re.sub(r"\s*[-|]\s*포켓몬스토어.*$", "", value).strip()
            if value:
                return value

    return "상품명 확인 불가"


def extract_product_no(url: str) -> Optional[str]:
    query = parse_qs(urlparse(url).query)
    product_no = query.get("productNo", [None])[0]
    if product_no and product_no.isdigit():
        return product_no

    match = re.search(r"/products?/(\d+)", url)
    return match.group(1) if match else None


def load_shopby_client_id(product_url: str) -> str:
    parsed = urlparse(product_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    response = requests.get(
        f"{origin}/environment.json",
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    env = response.json()
    client_id = env.get("clientId")
    if not client_id:
        raise ValueError("environment.json does not contain clientId.")
    return client_id


def shopby_headers(client_id: str) -> Dict[str, str]:
    return {
        **HEADERS,
        "accept": "application/json",
        "version": "1.0",
        "clientid": client_id,
        "platform": "PC",
    }


def flatten_options(options: Any) -> List[Dict[str, Any]]:
    if not isinstance(options, dict):
        return []

    flat_options = options.get("flatOptions")
    if isinstance(flat_options, list):
        return [item for item in flat_options if isinstance(item, dict)]

    multi_level_options = options.get("multiLevelOptions")
    if isinstance(multi_level_options, list):
        return [item for item in multi_level_options if isinstance(item, dict)]

    return []


def judge_shopby_options(options: Any) -> str:
    flat_options = flatten_options(options)
    if not flat_options:
        return "unknown"

    has_sold_out_option = False
    for option in flat_options:
        sale_type = str(option.get("saleType", "")).upper()
        stock_count = option.get("stockCnt")
        forced_sold_out = bool(option.get("forcedSoldOut"))

        try:
            stock_count_number = int(stock_count)
        except (TypeError, ValueError):
            stock_count_number = 0

        if stock_count_number > 0 and sale_type not in {"SOLDOUT", "STOP"} and not forced_sold_out:
            return "in_stock"

        if sale_type in {"SOLDOUT", "STOP"} or forced_sold_out or stock_count_number <= 0:
            has_sold_out_option = True

    return "out_of_stock" if has_sold_out_option else "unknown"


def fetch_product_from_shopby_api(url: str) -> Optional[Dict[str, str]]:
    product_no = extract_product_no(url)
    if not product_no:
        return None

    client_id = load_shopby_client_id(url)
    headers = shopby_headers(client_id)

    product_response = requests.get(
        f"{SHOPBY_API_BASE_URL}/products/{product_no}",
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    product_response.raise_for_status()
    product_data = product_response.json()

    options_response = requests.get(
        f"{SHOPBY_API_BASE_URL}/products/{product_no}/options",
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    options_response.raise_for_status()
    options_data = options_response.json()

    product_name = (
        product_data.get("baseInfo", {}).get("productName")
        if isinstance(product_data, dict)
        else None
    )

    return {
        "name": unescape(product_name or "상품명 확인 불가"),
        "status": judge_shopby_options(options_data),
    }


def judge_stock_status(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    page_text = normalize_text(soup.get_text(" ", strip=True))
    sold_out = text_matches_any(page_text, SOLD_OUT_PATTERNS)
    buyable = text_matches_any(page_text, BUYABLE_PATTERNS)

    if sold_out:
        return "out_of_stock"
    if buyable:
        return "in_stock"
    return "unknown"


def fetch_product(url: str) -> Dict[str, str]:
    api_result = fetch_product_from_shopby_api(url)
    if api_result and api_result["status"] != "unknown":
        return api_result

    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding

    soup = BeautifulSoup(response.text, "html.parser")
    return {
        "name": extract_product_name(soup),
        "status": judge_stock_status(response.text),
    }


def send_ntfy_alert(product_name: str, url: str) -> None:
    if not NTFY_TOPIC_URL:
        print("NTFY_TOPIC_URL is not set. Skipping notification.")
        return

    message = f"🔥 포켓몬스토어 재고 발견!\n상품명: {product_name}\nURL: {url}"
    headers = {
        "Title": "포켓몬스토어 재고 발견",
        "Tags": "fire,shopping_cart",
        "Priority": "high",
    }

    response = requests.post(
        NTFY_TOPIC_URL,
        data=message.encode("utf-8"),
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


def should_alert(previous_status: Optional[str], current_status: str) -> bool:
    if current_status != "in_stock":
        return False
    if previous_status == "out_of_stock":
        return True
    return previous_status is None and ALERT_ON_FIRST_IN_STOCK


def monitor_once() -> int:
    products = load_products()
    state = load_state()
    product_state = state.setdefault("products", {})

    print(f"Loaded {len(products)} product(s).")

    for index, url in enumerate(products, start=1):
        print(f"[{index}/{len(products)}] Checking {url}")

        previous = product_state.get(url, {})
        previous_status = previous.get("status") if isinstance(previous, dict) else None

        try:
            result = fetch_product(url)
            current_status = result["status"]
            product_name = result["name"]

            print(f"  name={product_name}")
            print(f"  status={previous_status or 'none'} -> {current_status}")

            if should_alert(previous_status, current_status):
                send_ntfy_alert(product_name, url)
                print("  notification sent")

            product_state[url] = {
                "name": product_name,
                "status": current_status,
                "last_checked_at": now_iso(),
                "last_error": None,
            }
        except Exception as exc:
            print(f"  error: {exc}")
            product_state[url] = {
                **(previous if isinstance(previous, dict) else {}),
                "last_checked_at": now_iso(),
                "last_error": str(exc),
            }

        if index < len(products):
            sleep_seconds = random.uniform(SLEEP_MIN_SECONDS, SLEEP_MAX_SECONDS)
            print(f"  sleeping {sleep_seconds:.1f}s")
            time.sleep(sleep_seconds)

    state["updated_at"] = now_iso()
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(monitor_once())
