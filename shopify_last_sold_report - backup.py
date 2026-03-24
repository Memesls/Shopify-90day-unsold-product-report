"""
Shopify Last Sold Report Generator
------------------------------------
Generates a CSV per store showing every product variant alongside:
  - Current stock level
  - Last date the variant was sold
  - Days since last sale (or "Never Sold" if no history)

Output: one CSV file per store, e.g. "report_your-store.csv"
Sorted by: Days Since Last Sale (highest first — worst offenders at the top)

Requirements:
  pip install requests

Usage:
  1. Fill in the STORES list below with your store credentials.
  2. Optionally adjust THRESHOLD_DAYS to highlight items in the report.
  3. Run:  python shopify_last_sold_report.py
"""

import requests
import csv
import os
from datetime import datetime, timezone

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

STORES = [
    {
        "shop": "your-store-one.myshopify.com",
        "token": "your_admin_api_token_one",
    },
    # Add more stores here:
    # {
    #     "shop": "your-store-two.myshopify.com",
    #     "token": "your_admin_api_token_two",
    # },
]

API_VERSION = "2024-01"

# Items not sold within this many days will be flagged in the CSV
THRESHOLD_DAYS = 90

# Output directory for CSVs (created if it doesn't exist)
OUTPUT_DIR = r"D:\Claude Projects\Knocking 90-day unsold inventory\CSV files"

# ─────────────────────────────────────────────────────────────────────────────


def get_headers(token):
    return {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }


def paginate(url, key, headers, params=None):
    """Fetch all pages from a Shopify REST endpoint."""
    results = []
    while url:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        results.extend(response.json().get(key, []))
        link = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    params = None  # Params are baked into the next URL
    return results


def get_all_variants(base_url, headers):
    """Return every variant in the store with product context."""
    products = paginate(
        f"{base_url}/products.json",
        "products",
        headers,
        params={"limit": 250, "fields": "id,title,variants,vendor"},
    )
    variants = []
    for product in products:
        for v in product.get("variants", []):
            variants.append({
                "product_id": product["id"],
                "product_title": product["title"],
                "vendor": product.get("vendor", ""),
                "variant_id": v["id"],
                "variant_title": v.get("title", ""),
                "sku": v.get("sku", ""),
                "inventory_quantity": v.get("inventory_quantity", 0),
            })
    return variants


def get_last_sold_map(base_url, headers):
    """
    Returns a dict: { variant_id -> last_sold_datetime }
    by scanning ALL orders (all statuses) in reverse chronological order.
    We look at line items to find when each variant last appeared in an order.
    """
    print("  Fetching all orders (this may take a while for large stores)...")
    orders = paginate(
        f"{base_url}/orders.json",
        "orders",
        headers,
        params={
            "status": "any",
            "limit": 250,
            "fields": "id,created_at,line_items",
        },
    )

    last_sold = {}
    for order in orders:
        order_date_str = order.get("created_at")
        if not order_date_str:
            continue
        order_date = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
        for item in order.get("line_items", []):
            vid = item.get("variant_id")
            if not vid:
                continue
            if vid not in last_sold or order_date > last_sold[vid]:
                last_sold[vid] = order_date

    print(f"  Found {len(last_sold)} variants with at least one order.")
    return last_sold


def days_since(dt):
    """Return integer days between dt and now (UTC)."""
    now = datetime.now(timezone.utc)
    return (now - dt).days


def generate_report(store):
    shop = store["shop"]
    token = store["token"]
    base_url = f"https://{shop}/admin/api/{API_VERSION}"
    headers = get_headers(token)

    print(f"\n{'='*60}")
    print(f"  Store: {shop}")
    print(f"{'='*60}")

    print("  Fetching product catalog...")
    variants = get_all_variants(base_url, headers)
    print(f"  Found {len(variants)} variants total.")

    last_sold_map = get_last_sold_map(base_url, headers)

    now = datetime.now(timezone.utc)
    rows = []
    for v in variants:
        vid = v["variant_id"]
        if vid in last_sold_map:
            last_sold_dt = last_sold_map[vid]
            last_sold_str = last_sold_dt.strftime("%Y-%m-%d")
            days = days_since(last_sold_dt)
            flag = "⚠ OVER THRESHOLD" if days >= THRESHOLD_DAYS else ""
        else:
            last_sold_str = "Never Sold"
            days = None
            flag = "⚠ NEVER SOLD"

        rows.append({
            "Product Title": v["product_title"],
            "Product Variant": v["variant_title"],
            "SKU": v["sku"],
            "Product Vendor": v["vendor"],
            "Available Inventory": v["inventory_quantity"],
            "Last Sold Date": last_sold_str,
            # Store a sort key: never-sold items go to the very top (999999)
            "_sort_key": days if days is not None else 999999,
            "Days Since Last Sale": days if days is not None else "Never",
            f"Over {THRESHOLD_DAYS}-Day Threshold?": flag,
        })

    # Sort: most stale at the top
    rows.sort(key=lambda r: r["_sort_key"], reverse=True)
    for r in rows:
        del r["_sort_key"]

    # Write CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_name = shop.replace(".myshopify.com", "").replace(".", "-")
    filename = os.path.join(OUTPUT_DIR, f"report_{safe_name}_{now.strftime('%Y%m%d')}.csv")

    fieldnames = [
        "Product Title", "Product Variant", "SKU", "Product Vendor",
        "Available Inventory", "Last Sold Date", "Days Since Last Sale",
        f"Over {THRESHOLD_DAYS}-Day Threshold?",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    never_sold = sum(1 for r in rows if r["Last Sold Date"] == "Never Sold")
    over_threshold = sum(1 for r in rows if r[f"Over {THRESHOLD_DAYS}-Day Threshold?"])
    print(f"\n  ✓ Report saved: {filename}")
    print(f"  Summary:")
    print(f"    Total variants:           {len(rows)}")
    print(f"    Never sold:               {never_sold}")
    print(f"    Not sold in {THRESHOLD_DAYS}+ days:    {over_threshold}")

    return filename


def main():
    print("\n╔══════════════════════════════════════════════╗")
    print("║   Shopify Last Sold Report Generator         ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  Threshold: {THRESHOLD_DAYS} days")
    print(f"  Stores to process: {len(STORES)}")

    generated = []
    for store in STORES:
        try:
            path = generate_report(store)
            generated.append(path)
        except requests.exceptions.HTTPError as e:
            print(f"\n  ✗ HTTP error for {store['shop']}: {e}")
        except Exception as e:
            print(f"\n  ✗ Unexpected error for {store['shop']}: {e}")

    print(f"\n{'='*60}")
    print(f"  Done. {len(generated)} report(s) generated in '{OUTPUT_DIR}/'")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
