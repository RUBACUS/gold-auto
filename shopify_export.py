import requests
import time
import json
import csv
import os
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

# ── Confirmed metafield keys for taara-laxmii.myshopify.com ──
# These are on the PRODUCT node — not the variant node.
METAFIELD_KEYS = {
    "gold_weight_14kt": "custom.14kt_metal_weight",
    "gold_weight_18kt": "custom.18kt_metal_weight",
    "gold_weight_9kt":  "custom.9kt_metal_weight",
    "diamond_weight":   "custom.diamond_total_weight",
    "gemstone_weight":  "custom.gemstone_total_weight",
}

MIN_EXPECTED_ROWS = int(os.environ.get("MIN_EXPECTED_VARIANT_ROWS", "1000"))

BULK_EXPORT_TIMEOUT_MINUTES = int(os.environ.get("BULK_EXPORT_TIMEOUT_MINUTES", "15"))
BULK_EXPORT_POLL_INTERVAL = int(os.environ.get("BULK_EXPORT_POLL_INTERVAL_SECONDS", "10"))


def _ts():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _graphql_request(payload, retries=3):
    """Makes a GraphQL request with retry on 429/500 errors."""
    for attempt in range(retries):
        try:
            resp = requests.post(GRAPHQL_URL, headers=HEADERS,
                                 json=payload, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                print(f"[{_ts()}] [Export] Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
                print(f"[{_ts()}] [Export] Shopify server error {resp.status_code}. "
                      f"Retry {attempt+1}/{retries} in {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise Exception(f"Shopify API unreachable after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def submit_bulk_export():
    """Submits the bulk export job. Returns operation ID."""
    mutation = """
    mutation {
      bulkOperationRunQuery(
        query: \"\"\"
        {
          products {
            edges {
              node {
                id
                handle
                title
                status
                vendor
                productType
                tags
                metafields {
                  edges {
                    node {
                      namespace
                      key
                      value
                    }
                  }
                }
                variants {
                  edges {
                    node {
                      id
                      sku
                      price
                      compareAtPrice
                      position
                      selectedOptions {
                        name
                        value
                      }
                    }
                  }
                }
              }
            }
          }
        }
        \"\"\"
      ) {
        bulkOperation { id status }
        userErrors { field message }
      }
    }
    """
    data = _graphql_request({"query": mutation})
    errors = data.get("data", {}).get("bulkOperationRunQuery", {}).get("userErrors", [])
    if errors:
        raise Exception(f"Bulk export submission error: {errors}")

    op_id = data["data"]["bulkOperationRunQuery"]["bulkOperation"]["id"]
    print(f"[{_ts()}] [Export] Bulk job submitted. ID: {op_id}")
    return op_id


def poll_until_complete(max_wait_minutes=None):
    """Polls until job is COMPLETED. Returns download URL."""
    if max_wait_minutes is None:
        max_wait_minutes = BULK_EXPORT_TIMEOUT_MINUTES

    query = """
    {
      currentBulkOperation {
        id status errorCode objectCount fileSize url
      }
    }
    """
    max_attempts = (max_wait_minutes * 60) // BULK_EXPORT_POLL_INTERVAL
    for attempt in range(max_attempts):
        time.sleep(BULK_EXPORT_POLL_INTERVAL)
        data = _graphql_request({"query": query})

        # CRITICAL: currentBulkOperation returns null when no operation is running.
        # Must check for null before accessing fields — otherwise TypeError crash.
        op = data["data"].get("currentBulkOperation")
        if op is None:
            print(f"[{_ts()}] [Export] Poll {attempt+1}: No bulk operation running yet. Waiting...")
            continue

        status = op.get("status", "UNKNOWN")
        count = op.get("objectCount", 0)

        print(f"[{_ts()}] [Export] Poll {attempt+1}: {status} — {count} objects")

        if status == "COMPLETED":
            url = op.get("url")
            if not url:
                raise Exception("Job completed but no download URL returned.")
            print(f"[{_ts()}] [Export] Ready. {count} objects exported.")
            return url

        if status == "FAILED":
            raise Exception(f"Bulk export FAILED. Error: {op.get('errorCode')}")

        if status not in ("RUNNING", "CREATED"):
            raise Exception(f"Unexpected bulk operation status: {status}")

    raise Exception(f"Bulk export timed out after {max_wait_minutes} minutes.")


def download_and_convert(download_url, output_path):
    """Downloads .jsonl, converts to CSV. Returns (csv_path, row_count)."""
    print(f"[{_ts()}] [Export] Downloading .jsonl from Shopify...")

    resp = requests.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()

    raw_path = output_path.replace(".csv", "_raw.jsonl")
    try:
        with open(raw_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"[{_ts()}] [Export] Converting .jsonl -> CSV...")

        products = {}            # product gid -> product object
        product_metafields = {}  # product gid -> {namespace.key: value}
        variants = []            # list of variant objects

        # CRITICAL: In Shopify Bulk Operations JSONL format, nested connections
        # (metafields, variants) appear as SEPARATE LINES — not nested inside the
        # parent object. Each line has __parentId pointing to its parent's GID.
        with open(raw_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)

                if "__parentId" not in obj:
                    # Top-level product object (no parent)
                    products[obj["id"]] = obj

                elif "namespace" in obj and "key" in obj:
                    # Metafield line — has namespace + key fields
                    parent_id = obj["__parentId"]
                    if parent_id not in product_metafields:
                        product_metafields[parent_id] = {}
                    mf_key = f"{obj['namespace']}.{obj['key']}"
                    product_metafields[parent_id][mf_key] = obj.get("value", "")

                else:
                    # Variant line — has price, sku, option fields
                    variants.append(obj)

        rows = []
        for variant in variants:
            parent_id = variant.get("__parentId")
            product = products.get(parent_id, {})

            # Get this product's metafields (confirmed: metafields are product-level)
            mf = product_metafields.get(parent_id, {})

            # Extract numeric Shopify ID from gid:// URI
            variant_id = variant.get("id", "").split("/")[-1]
            product_id = product.get("id", "").split("/")[-1]

            # Build CSV row matching exact 82-column Shopify export format
            row = {
                "Handle":                       product.get("handle", ""),
                "Title":                        product.get("title", ""),
                "Body (HTML)":                  "",
                "Vendor":                       product.get("vendor", ""),
                "Product Category":             "",
                "Type":                         product.get("productType", ""),
                "Tags":                         ", ".join(product.get("tags", [])) if isinstance(product.get("tags"), list) else product.get("tags", ""),
                "Published":                    "",
                "Option1 Name":                 "",
                # selectedOptions is a list [{name, value}, ...] — map by position
                "Option1 Value": (variant.get("selectedOptions") or [{}])[0].get("value", ""),
                "Option1 Linked To":            "",
                "Option2 Name":                 "",
                "Option2 Value": (variant.get("selectedOptions") or [{}, {}])[1].get("value", "") if len(variant.get("selectedOptions") or []) > 1 else "",
                "Option2 Linked To":            "",
                "Option3 Name":                 "",
                "Option3 Value": (variant.get("selectedOptions") or [{}, {}, {}])[2].get("value", "") if len(variant.get("selectedOptions") or []) > 2 else "",
                "Option3 Linked To":            "",
                "Variant SKU":                  variant.get("sku", ""),
                "Variant Grams":                "",
                "Variant Inventory Tracker":    "",
                "Variant Inventory Qty":        "",
                "Variant Inventory Policy":     "",
                "Variant Fulfillment Service":  "",
                "Variant Price":                variant.get("price", ""),
                "Variant Compare At Price":     variant.get("compareAtPrice", ""),
                "Variant Requires Shipping":    "",
                "Variant Taxable":              "",
                "Unit Price Total Measure":     "",
                "Unit Price Total Measure Unit": "",
                "Unit Price Base Measure":      "",
                "Unit Price Base Measure Unit":  "",
                "Variant Barcode":              "",
                "Image Src":                    "",
                "Image Position":               "",
                "Image Alt Text":               "",
                "Gift Card":                    "",
                "SEO Title":                    "",
                "SEO Description":              "",
                "Google Shopping / Google Product Category": "",
                "Google Shopping / Gender":     "",
                "Google Shopping / Age Group":  "",
                "Google Shopping / MPN":        "",
                "Google Shopping / Condition":  "",
                "Google Shopping / Custom Product": "",
                "Google Shopping / Custom Label 0": "",
                "Google Shopping / Custom Label 1": "",
                "Google Shopping / Custom Label 2": "",
                "Google Shopping / Custom Label 3": "",
                "Google Shopping / Custom Label 4": "",
                # Col 50 — read by update_prices.py for 14KT pricing
                "14KT Metal Weight (product.metafields.custom.14kt_metal_weight)":
                    mf.get(METAFIELD_KEYS["gold_weight_14kt"], ""),
                # Col 51 — read by update_prices.py for 18KT pricing
                "18KT Metal Weight (product.metafields.custom.18kt_metal_weight)":
                    mf.get(METAFIELD_KEYS["gold_weight_18kt"], ""),
                # Col 52 — read by update_prices.py for 9KT pricing
                "9KT Metal weight (product.metafields.custom.9kt_metal_weight)":
                    mf.get(METAFIELD_KEYS["gold_weight_9kt"], ""),
                "Diamond Count (product.metafields.custom.diamond_count)":         "",
                "Diamond Quality (product.metafields.custom.diamond_quality)":     "",
                # Col 55 — read by update_prices.py for diamond pricing
                "Diamond Total Weight (product.metafields.custom.diamond_total_weight)":
                    mf.get(METAFIELD_KEYS["diamond_weight"], ""),
                "Diamond Weight Filter (product.metafields.custom.diamond_weight_filter)": "",
                "Fancy Diamond Weight (product.metafields.custom.fancy_diamond_weight)":   "",
                "Gemstone Color (product.metafields.custom.gemstone_color)":               "",
                "Gemstone Count (product.metafields.custom.gemstone_count)":               "",
                # Col 60 — read by update_prices.py for gemstone pricing
                "Gemstone Total Weight (product.metafields.custom.gemstone_total_weight)":
                    mf.get(METAFIELD_KEYS["gemstone_weight"], ""),
                "Gender (product.metafields.custom.gender)":                               "",
                "Love this piece? (product.metafields.custom.love_this_piece)":            "",
                "Pendant Chain Note (product.metafields.custom.pendant_chain_note)":       "",
                "Preferred by (product.metafields.custom.preferred_by)":                   "",
                "Product Standard Size (product.metafields.custom.product_standard_size)": "",
                "Product Type (product.metafields.custom.product_type)":                   "",
                "Round Diamond Weight (product.metafields.custom.round_diamond_weight)":   "",
                "Showcase Tags (product.metafields.custom.showcase_tags)":                 "",
                "Sibling Product Link (product.metafields.custom.sibling_product_link)":   "",
                "Sibling Product Type (product.metafields.custom.sibling_product_type)":   "",
                "SKU (product.metafields.custom.sku)":                                     "",
                "Google: Custom Product (product.metafields.mm-google-shopping.custom_product)": "",
                "Ring size (product.metafields.shopify.ring-size)":                        "",
                "Complementary products (product.metafields.shopify--discovery--product_recommendation.complementary_products)": "",
                "Related products (product.metafields.shopify--discovery--product_recommendation.related_products)": "",
                "Related products settings (product.metafields.shopify--discovery--product_recommendation.related_products_display)": "",
                "Search product boosts (product.metafields.shopify--discovery--product_search_boost.queries)": "",
                "Variant Image":        "",
                "Variant Weight Unit":  "",
                "Variant Tax Code":     "",
                "Cost per item":        "",
                "Status":               product.get("status", ""),
                # Extra cols for automation (appended at end, cols 83-84)
                "Variant ID":           variant_id,
                "Product ID":           product_id,
            }
            rows.append(row)
    finally:
        # Always clean up raw JSONL file
        if os.path.exists(raw_path):
            os.remove(raw_path)

    if not rows:
        raise Exception("Conversion produced 0 rows. The .jsonl file may be empty or malformed.")

    if len(rows) < MIN_EXPECTED_ROWS:
        raise Exception(
            f"Only {len(rows)} rows found — expected at least {MIN_EXPECTED_ROWS}. "
            f"Data may be incomplete. Aborting to prevent bad pricing."
        )

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"[{_ts()}] [Export] CSV saved: {output_path} ({len(rows)} rows)")
    return output_path, len(rows)


def fetch_fresh_shopify_csv(output_dir="uploads"):
    """
    Full pipeline: submit -> poll -> download -> convert.
    Returns (csv_path, row_count).
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(IST).strftime("%d%b%Y_%H%M")
    output_path = os.path.join(output_dir, f"shopify_export_{ts}.csv")

    print(f"\n{'=' * 55}")
    print(f"[{_ts()}] [Export] Starting fresh Shopify product export...")
    print(f"{'=' * 55}")

    submit_bulk_export()
    download_url = poll_until_complete()
    csv_path, row_count = download_and_convert(download_url, output_path)

    print(f"[{_ts()}] [Export] Done! {row_count} variants ready at: {csv_path}")
    return csv_path, row_count


if __name__ == "__main__":
    path, count = fetch_fresh_shopify_csv()
    print(f"Exported {count} rows to {path}")
