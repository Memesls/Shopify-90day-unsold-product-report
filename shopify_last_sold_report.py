"""
Shopify Last Sold Report Generator (v2 - Optimized)
------------------------------------
Generates a CSV per store showing product variants that have NOT sold in the
last THRESHOLD_DAYS days, alongside:
  - Current stock level
  - Last date the variant was sold (within LAST_SOLD_LOOKBACK_DAYS)
  - Days since last sale (or "Never Sold" if no history in lookback window)
  - Last date inventory was manually adjusted by an admin or app

Output: one CSV file per store, e.g. "report_your-store-YYYYMMDD.csv"
Sorted by: Days Since Last Sale (highest first — worst offenders at the top)

Key improvements over v1:
  - Single order scan instead of two full-history fetches
  - Batched GraphQL queries (N/25 requests instead of N requests)
  - Automatic retry with backoff on rate limits (REST 429 + GraphQL THROTTLED)
  - All stores processed in parallel
  - Configurable order history lookback to cap runtime on large stores

Requirements:
  pip install requests

Usage:
  1. Fill in the STORES list below with your store credentials.
  2. Optionally adjust THRESHOLD_DAYS, LAST_SOLD_LOOKBACK_DAYS, etc.
  3. Run:  python shopify_last_sold_report.py
"""

import csv
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ─── CONFIGURATION ───────────────────────────────────────────────────────────

STORES = [
    {
        "shop": "your-store-one.myshopify.com",
        "token": "your_admin_api_token_one",
    },
    # {
    #     "shop": "your-store-two.myshopify.com",
    #     "token": "your_admin_api_token_two",
    # },
    # {
    #     "shop": "your-store-three.myshopify.com",
    #     "token": "your_admin_api_token_three",
    # },
]

API_VERSION = "2025-01"

# Variants NOT sold within this many days appear in the report
THRESHOLD_DAYS = 90

# How far back to scan order history when looking for a variant's last sale date.
# Variants with no sale in this window are shown as "Never Sold".
# Increase for deeper history; decrease to cap runtime on very large stores.
LAST_SOLD_LOOKBACK_DAYS = 365  # 1 year

# Items per batched GraphQL request (25–50 recommended; lower = safer on cost limits)
GRAPHQL_BATCH_SIZE = 25

# Process this many stores simultaneously (one thread per store)
STORE_WORKERS = 3

# Max retries for rate-limited or transient API errors
MAX_RETRIES = 6

# Output directory for CSVs (created if it doesn't exist)
OUTPUT_DIR = r"D:\Claude Projects\Knocking 90-day unsold inventory\CSV files"

# ─────────────────────────────────────────────────────────────────────────────

_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    """Thread-safe print so output from parallel stores doesn't interleave."""
    with _print_lock:
        print(*args, **kwargs)


def make_session():
    """Create a requests.Session with connection pooling."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=4,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    return session


def api_request(session, method, url, headers, retries=MAX_RETRIES, **kwargs):
    """
    Make an API request with automatic retry on:
      - HTTP 429 (rate limit) — honours the Retry-After header
      - HTTP 5xx (server error) — exponential backoff
      - ConnectionError — transient network issue
    """
    for attempt in range(retries):
        try:
            response = session.request(method, url, headers=headers, **kwargs)
        except requests.exceptions.ConnectionError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", 2 ** attempt))
            safe_print(f"    [Rate limited] Waiting {wait:.1f}s before retry...")
            time.sleep(wait)
            continue

        if response.status_code >= 500 and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue

        response.raise_for_status()
        return response

    raise Exception(f"Request failed after {retries} attempts: {url}")


def paginate(url, key, session, headers, params=None):
    """Fetch all pages from a Shopify REST endpoint, following Link headers."""
    results = []
    while url:
        response = api_request(session, "GET", url, headers, params=params)
        results.extend(response.json().get(key, []))
        link = response.headers.get("Link", "")
        url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    params = None  # Next-page URL already contains params
    return results


# ─── GraphQL ─────────────────────────────────────────────────────────────────

# Batched query: fetch the 50 most recent inventory adjustments per item.
# Using `last: 50` ensures we get the newest adjustments even on items with
# a long history (the original `first: 50` would return the oldest 50).
ADJ_BATCH_QUERY = """
query getAdjustments($ids: [ID!]!) {
  nodes(ids: $ids) {
    id
    ... on InventoryItem {
      inventoryAdjustmentGroups(last: 50) {
        edges {
          node {
            createdAt
            reason
            staffMember { id }
            app { id }
          }
        }
      }
    }
  }
}
"""


def graphql_request(session, graphql_url, headers, query, variables):
    """
    Make a GraphQL request with retry on throttling errors.
    Also proactively slows down when the cost bucket is running low.
    """
    for attempt in range(MAX_RETRIES):
        response = api_request(
            session, "POST", graphql_url, headers,
            json={"query": query, "variables": variables},
        )
        data = response.json()

        # Handle GraphQL-level errors (e.g. THROTTLED)
        if "errors" in data:
            throttled = any(
                e.get("extensions", {}).get("code") == "THROTTLED"
                for e in data["errors"]
            )
            if throttled and attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                safe_print(f"    [GraphQL throttled] Waiting {wait}s before retry...")
                time.sleep(wait)
                continue
            raise Exception(f"GraphQL errors: {data['errors']}")

        # Proactively slow down when cost bucket is nearly depleted
        throttle = (
            data.get("extensions", {})
                .get("cost", {})
                .get("throttleStatus", {})
        )
        available = throttle.get("currentlyAvailable", 1000)
        restore_rate = throttle.get("restoreRate", 50)
        if available < 200 and restore_rate > 0:
            wait = (200 - available) / restore_rate
            time.sleep(wait)

        return data

    raise Exception(f"GraphQL request failed after {MAX_RETRIES} attempts.")


# ─── Data fetching ────────────────────────────────────────────────────────────

def get_all_variants(base_url, session, headers):
    """Return every variant in the store with its product context."""
    products = paginate(
        f"{base_url}/products.json",
        "products",
        session,
        headers,
        params={"limit": 250, "fields": "id,title,variants,vendor"},
    )
    variants = []
    for product in products:
        for v in product.get("variants", []):
            variants.append({
                "product_id":           product["id"],
                "product_title":        product["title"],
                "vendor":               product.get("vendor", ""),
                "variant_id":           v["id"],
                "variant_title":        v.get("title", ""),
                "sku":                  v.get("sku", ""),
                "inventory_item_id":    v.get("inventory_item_id"),
                "inventory_quantity":   v.get("inventory_quantity", 0),
            })
    return variants


def get_order_data(base_url, session, headers):
    """
    Single-pass order scan returning:
      recently_sold_ids  — variant IDs sold within THRESHOLD_DAYS (to exclude)
      last_sold_map      — { variant_id: most_recent_sale_datetime }

    Order history is capped at LAST_SOLD_LOOKBACK_DAYS to limit runtime on
    stores with years of order history.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=THRESHOLD_DAYS)
    lookback_start = now - timedelta(days=LAST_SOLD_LOOKBACK_DAYS)
    lookback_start_str = lookback_start.strftime("%Y-%m-%dT%H:%M:%SZ")

    safe_print(
        f"  Scanning orders (last {LAST_SOLD_LOOKBACK_DAYS} days)..."
    )

    orders = paginate(
        f"{base_url}/orders.json",
        "orders",
        session,
        headers,
        params={
            "status":           "any",
            "limit":            250,
            "created_at_min":   lookback_start_str,
            "fields":           "id,created_at,line_items",
        },
    )

    safe_print(f"  Processed {len(orders):,} orders.")

    last_sold_map = {}
    for order in orders:
        order_date_str = order.get("created_at")
        if not order_date_str:
            continue
        order_date = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
        for item in order.get("line_items", []):
            vid = item.get("variant_id")
            if vid and (vid not in last_sold_map or order_date > last_sold_map[vid]):
                last_sold_map[vid] = order_date

    recently_sold_ids = {
        vid for vid, dt in last_sold_map.items() if dt >= cutoff
    }

    safe_print(
        f"  Variants sold in last {THRESHOLD_DAYS} days: "
        f"{len(recently_sold_ids):,} (excluded from report)"
    )
    return recently_sold_ids, last_sold_map


def get_last_admin_restock_map(shop, session, headers, inventory_item_ids):
    """
    Batched GraphQL query for the most recent manual inventory correction
    (reason='correction' by a staff member or app) per inventory item.

    Returns { inventory_item_id (int): datetime }
    """
    graphql_url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    last_restock = {}
    total = len(inventory_item_ids)

    safe_print(
        f"  Querying inventory adjustment history "
        f"({total:,} variants, {GRAPHQL_BATCH_SIZE} per request)..."
    )

    for batch_start in range(0, total, GRAPHQL_BATCH_SIZE):
        batch = inventory_item_ids[batch_start: batch_start + GRAPHQL_BATCH_SIZE]
        gids = [f"gid://shopify/InventoryItem/{iid}" for iid in batch]

        data = graphql_request(session, graphql_url, headers, ADJ_BATCH_QUERY, {"ids": gids})

        for node in data.get("data", {}).get("nodes", []):
            # `nodes()` returns null for IDs not found; non-InventoryItem nodes
            # won't have `inventoryAdjustmentGroups` (inline fragment won't match)
            if not node or "inventoryAdjustmentGroups" not in node:
                continue

            inv_item_id = int(node["id"].split("/")[-1])

            best_dt = None
            for edge in node["inventoryAdjustmentGroups"].get("edges", []):
                n = edge.get("node", {})
                if n.get("reason") != "correction":
                    continue
                if not n.get("staffMember") and not n.get("app"):
                    continue
                created_at_str = n.get("createdAt")
                if not created_at_str:
                    continue
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if best_dt is None or dt > best_dt:
                    best_dt = dt

            if best_dt is not None:
                last_restock[inv_item_id] = best_dt

        processed = min(batch_start + GRAPHQL_BATCH_SIZE, total)
        if processed % (GRAPHQL_BATCH_SIZE * 10) == 0 or processed >= total:
            safe_print(f"    Progress: {processed:,}/{total:,}")

    safe_print(
        f"  Found admin restock records for {len(last_restock):,}/{total:,} variants."
    )
    return last_restock


# ─── Report generation ────────────────────────────────────────────────────────

def days_since(dt):
    return (datetime.now(timezone.utc) - dt).days


def generate_report(store):
    shop    = store["shop"]
    token   = store["token"]
    base_url = f"https://{shop}/admin/api/{API_VERSION}"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type":           "application/json",
    }
    session = make_session()

    safe_print(f"\n{'='*60}")
    safe_print(f"  Store: {shop}")
    safe_print(f"{'='*60}")

    # 1. Product catalog
    safe_print("  Fetching product catalog...")
    all_variants = get_all_variants(base_url, session, headers)
    safe_print(f"  Found {len(all_variants):,} variants total.")

    # 2. Single-pass order scan
    recently_sold_ids, last_sold_map = get_order_data(base_url, session, headers)

    # 3. Filter: unsold within threshold AND currently in stock
    unsold_variants = [
        v for v in all_variants
        if v["variant_id"] not in recently_sold_ids
        and v["inventory_quantity"] > 0
    ]
    safe_print(
        f"  Variants not sold in last {THRESHOLD_DAYS} days (stock > 0): "
        f"{len(unsold_variants):,}"
    )

    if not unsold_variants:
        safe_print("  No unsold variants to report. Skipping.")
        return None

    # 4. Batched GraphQL: last admin inventory adjustment
    inventory_item_ids = [
        v["inventory_item_id"] for v in unsold_variants if v["inventory_item_id"]
    ]
    last_restock_map = get_last_admin_restock_map(shop, session, headers, inventory_item_ids)

    # 5. Build rows
    now = datetime.now(timezone.utc)
    rows = []
    for v in unsold_variants:
        vid    = v["variant_id"]
        inv_id = v["inventory_item_id"]

        if vid in last_sold_map:
            last_sold_dt  = last_sold_map[vid]
            last_sold_str = last_sold_dt.strftime("%Y-%m-%d")
            days          = days_since(last_sold_dt)
        else:
            last_sold_str = "Never Sold"
            days          = None

        if inv_id and inv_id in last_restock_map:
            last_restock_str = last_restock_map[inv_id].strftime("%Y-%m-%d")
        else:
            last_restock_str = "No Record"

        rows.append({
            "Product Title":            v["product_title"],
            "Product Variant":          v["variant_title"],
            "SKU":                      v["sku"],
            "Product Vendor":           v["vendor"],
            "Available Inventory":      v["inventory_quantity"],
            "Last Sold Date":           last_sold_str,
            "_sort_key":                days if days is not None else 999_999,
            "Days Since Last Sale":     days if days is not None else "Never",
            f"Over {THRESHOLD_DAYS}-Day Threshold?":
                "⚠ NEVER SOLD" if days is None else "⚠ OVER THRESHOLD",
            "Last Admin Inventory Adjustment": last_restock_str,
        })

    # Sort most-stale first
    rows.sort(key=lambda r: r["_sort_key"], reverse=True)
    for r in rows:
        del r["_sort_key"]

    # 6. Write CSV
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_name = shop.replace(".myshopify.com", "").replace(".", "-")
    filename = os.path.join(
        OUTPUT_DIR, f"report_{safe_name}_{now.strftime('%Y%m%d')}.csv"
    )

    fieldnames = [
        "Product Title", "Product Variant", "SKU", "Product Vendor",
        "Available Inventory", "Last Sold Date", "Days Since Last Sale",
        f"Over {THRESHOLD_DAYS}-Day Threshold?",
        "Last Admin Inventory Adjustment",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    never_sold  = sum(1 for r in rows if r["Last Sold Date"] == "Never Sold")
    no_restock  = sum(1 for r in rows if r["Last Admin Inventory Adjustment"] == "No Record")
    safe_print(f"\n  ✓ Report saved: {filename}")
    safe_print(f"  Summary:")
    safe_print(f"    Variants in report:              {len(rows):,}")
    safe_print(f"    Never sold (in lookback window): {never_sold:,}")
    safe_print(f"    No admin restock on record:      {no_restock:,}")

    return filename


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    print("\n╔══════════════════════════════════════════════╗")
    print("║  Shopify Last Sold Report Generator v2       ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  Threshold:        {THRESHOLD_DAYS} days")
    print(f"  Order lookback:   {LAST_SOLD_LOOKBACK_DAYS} days")
    print(f"  GraphQL batch:    {GRAPHQL_BATCH_SIZE} items/request")
    print(f"  Stores:           {len(STORES)}")
    print(f"  Parallel workers: {min(STORE_WORKERS, len(STORES))}")

    generated = []
    errors    = []
    workers   = min(STORE_WORKERS, len(STORES))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(generate_report, store): store for store in STORES}
        for future in as_completed(futures):
            store = futures[future]
            try:
                path = future.result()
                if path:
                    generated.append(path)
            except requests.exceptions.HTTPError as e:
                errors.append((store["shop"], str(e)))
                safe_print(f"\n  ✗ HTTP error for {store['shop']}: {e}")
            except Exception as e:
                errors.append((store["shop"], str(e)))
                safe_print(f"\n  ✗ Unexpected error for {store['shop']}: {e}")

    print(f"\n{'='*60}")
    print(f"  Done. {len(generated)} report(s) generated.")
    for path in generated:
        print(f"    {path}")
    if errors:
        print(f"\n  {len(errors)} store(s) failed:")
        for shop, err in errors:
            print(f"    {shop}: {err}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
