import requests
import time
import csv
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE")
SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN")
SHOPIFY_API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
GRAPHQL_URL = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
}

DELAY_BETWEEN_CALLS = float(os.environ.get("PUSH_DELAY_SECONDS", "0.2"))
MAX_RETRIES = int(os.environ.get("PUSH_MAX_RETRIES", "3"))
FAILURE_THRESHOLD_PERCENT = float(os.environ.get("PUSH_FAILURE_THRESHOLD_PERCENT", "5"))


def _ts():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _graphql_request(payload, retries=None):
    """Makes a GraphQL request with retry on rate limit and server errors."""
    if retries is None:
        retries = MAX_RETRIES
    for attempt in range(retries):
        try:
            resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                                 json=payload, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                print(f"[{_ts()}] [Push] Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                print(f"[{_ts()}] [Push] Server error {resp.status_code}. Retry in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise Exception(f"Shopify API unreachable: {e}")
            time.sleep(2 ** attempt)


def _build_variants_input(variants):
    """Builds the GraphQL variants input array string."""
    parts = []
    for v in variants:
        variant_id = f"gid://shopify/ProductVariant/{v['variant_id']}"
        parts.append(
            f'{{id: "{variant_id}", '
            f'price: "{v["price"]}", '
            f'compareAtPrice: "{v["compare_at_price"]}"}}'
        )
    return "[" + ", ".join(parts) + "]"


def push_prices(csv_path):
    """
    Reads the generated pricing CSV, groups variants by product,
    and pushes all prices to Shopify via GraphQL.
    Returns (products_updated, variants_updated, failed_products).
    """
    print(f"\n{'=' * 55}")
    print(f"[{_ts()}] [Push] Starting Shopify price push via GraphQL...")
    print(f"{'=' * 55}")

    # Group variants by product_id
    product_variants = defaultdict(list)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            variant_id = str(row.get("Variant ID", "")).strip()
            product_id = str(row.get("Product ID", "")).strip()
            price = str(row.get("Variant Price", "")).strip()
            compare = str(row.get("Variant Compare At Price", "")).strip()

            if not variant_id or not product_id or not price:
                continue

            product_variants[product_id].append({
                "variant_id": variant_id,
                "price": price,
                "compare_at_price": compare or price,
            })

    total_products = len(product_variants)
    success_count = 0
    variants_count = 0
    failed_products = []

    print(f"[{_ts()}] [Push] {total_products} products to update...")

    for idx, (product_id, variants) in enumerate(product_variants.items(), 1):
        gid = f"gid://shopify/Product/{product_id}"
        variants_input = _build_variants_input(variants)

        mutation = f"""
        mutation {{
          productVariantsBulkUpdate(
            productId: "{gid}",
            variants: {variants_input}
          ) {{
            productVariants {{ id price compareAtPrice }}
            userErrors {{ field message }}
          }}
        }}
        """

        try:
            data = _graphql_request({"query": mutation})
            result = data.get("data", {}).get("productVariantsBulkUpdate", {})
            errors = result.get("userErrors", [])

            if errors:
                print(f"[{_ts()}] [Push] Product {product_id} errors: {errors}")
                failed_products.append({"product_id": product_id, "errors": errors})
            else:
                success_count += 1
                variants_count += len(variants)

        except Exception as e:
            print(f"[{_ts()}] [Push] Product {product_id} failed: {e}")
            failed_products.append({"product_id": product_id, "errors": str(e)})

        # Progress log every 100 products
        if idx % 100 == 0:
            print(f"[{_ts()}] [Push] Progress: {idx}/{total_products} products processed...")

        time.sleep(DELAY_BETWEEN_CALLS)

    print(f"\n[{_ts()}] [Push] Done. {success_count}/{total_products} products updated.")
    print(f"[{_ts()}] [Push] Variants updated: {variants_count}")
    if failed_products:
        print(f"[{_ts()}] [Push] {len(failed_products)} products FAILED:")
        for fp in failed_products:
            print(f"       Product {fp['product_id']}: {fp['errors']}")

    return success_count, variants_count, failed_products


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python shopify_push.py <csv_path>")
        sys.exit(1)
    s, v, f = push_prices(sys.argv[1])
    print(f"Success: {s} products, {v} variants. Failed: {len(f)}")
