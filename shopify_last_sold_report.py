"""
Shopify Last Sold Report Generator (v3)
---------------------------------------
Two-phase pipeline: Phase 1 fetches all store data in parallel;
Phase 2 cross-references SKUs and writes .xlsx + per-store CSV files.

Requirements:
    pip install requests openpyxl

Usage:
    1. Fill in the STORES list with your store credentials and names.
    2. Adjust constants as needed.
    3. Run: python shopify_last_sold_report.py
"""

import csv
import os
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

STORES = [
    {
        "shop":  "your-store-one.myshopify.com",
        "token": "your_admin_api_token_one",
        "name":  "STORE1",
    },
    # {
    #     "shop":  "your-store-two.myshopify.com",
    #     "token": "your_admin_api_token_two",
    #     "name":  "STORE2",
    # },
]

API_VERSION             = "2025-01"
THRESHOLD_DAYS          = 90
LAST_SOLD_LOOKBACK_DAYS = 365
MIN_ADJUSTMENT_QUANTITY = 5       # Min absolute delta to qualify an adjustment
GRAPHQL_BATCH_SIZE      = 25
STORE_WORKERS           = 4
MAX_RETRIES             = 6

OUTPUT_XLSX_DIR = r"D:\Claude Projects\Knocking 90-day unsold inventory"
OUTPUT_CSV_DIR  = r"D:\Claude Projects\Knocking 90-day unsold inventory\CSV files"

FIELDNAMES = [
    "Product Title",
    "Product Variant",
    "SKU",
    "Vendor",
    "Available Inventory",
    "Last Sold Date",
    "Days Since Last Sale",
    "Last Inventory Adjustment",
    "Days Since Last Adjustment",
    "Adjusted By",
    "Previous Inventory",
    "Adjustment Quantity",
    "New Inventory",
    "Shared Inventory",
]

# ─────────────────────────────────────────────────────────────────────────────


def assign_store_names(stores):
    """
    Return { store_index: display_name } with uniqueness guaranteed.
    Duplicates get _2, _3, ... suffixes, skipping any suffix already
    claimed by another store entry.
    """
    assigned = {}
    used = set()
    for i, store in enumerate(stores):
        base = store["name"]
        name = base
        counter = 2
        while name in used:
            name = f"{base}_{counter}"
            counter += 1
        assigned[i] = name
        used.add(name)
    return assigned


# ─── HTTP UTILITIES ───────────────────────────────────────────────────────────

_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    """Thread-safe print so parallel store output does not interleave."""
    with _print_lock:
        print(*args, **kwargs)


def make_session():
    """Create a requests.Session with connection pooling."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=10)
    session.mount("https://", adapter)
    return session


def api_request(session, method, url, headers, retries=MAX_RETRIES, **kwargs):
    """
    HTTP request with automatic retry on:
      - 429 (rate limit)   — honours Retry-After header
      - 5xx (server error) — exponential backoff
      - ConnectionError    — transient network issue
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
            safe_print(f"    [Rate limited] Waiting {wait:.1f}s...")
            time.sleep(wait)
            continue

        if response.status_code >= 500 and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue

        response.raise_for_status()
        return response

    raise Exception(f"Request failed after {retries} attempts: {url}")


def paginate(url, key, session, headers, params=None):
    """Fetch all pages from a Shopify REST endpoint via Link headers."""
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
                    params = None
    return results


def graphql_request(session, graphql_url, headers, query, variables):
    """
    GraphQL POST with retry on THROTTLED errors and proactive cost-bucket throttling.
    """
    for attempt in range(MAX_RETRIES):
        response = api_request(
            session, "POST", graphql_url, headers,
            json={"query": query, "variables": variables},
        )
        data = response.json()

        if "errors" in data:
            throttled = any(
                e.get("extensions", {}).get("code") == "THROTTLED"
                for e in data["errors"]
            )
            if throttled and attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                safe_print(f"    [GraphQL throttled] Waiting {wait}s...")
                time.sleep(wait)
                continue
            raise Exception(f"GraphQL errors: {data['errors']}")

        throttle = (
            data.get("extensions", {})
                .get("cost", {})
                .get("throttleStatus", {})
        )
        available    = throttle.get("currentlyAvailable", 1000)
        restore_rate = throttle.get("restoreRate", 50)
        if available < 200:
            wait = (200 - available) / restore_rate if restore_rate > 0 else 2
            time.sleep(wait)

        return data

    raise Exception(f"GraphQL request failed after {MAX_RETRIES} attempts.")


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
            staffMember { displayName }
            app { title }
            changes {
              delta
              quantityAfterChange
            }
          }
        }
      }
    }
  }
}
"""


# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def get_all_variants(base_url, session, headers):
    """Return every active variant with its product context."""
    products = paginate(
        f"{base_url}/products.json",
        "products",
        session,
        headers,
        params={
            "status": "active",
            "limit":  250,
            "fields": "id,title,vendor,created_at,variants",
        },
    )
    variants = []
    for product in products:
        created_at_str = product.get("created_at", "")
        created_at = (
            datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            if created_at_str else None
        )
        for v in product.get("variants", []):
            variants.append({
                "product_id":         product["id"],
                "product_title":      product["title"],
                "vendor":             product.get("vendor", ""),
                "product_created_at": created_at,
                "variant_id":         v["id"],
                "variant_title":      v.get("title", ""),
                "sku":                v.get("sku", ""),
                "inventory_item_id":  v.get("inventory_item_id"),
                "inventory_quantity": v.get("inventory_quantity", 0),
            })
    return variants


def get_order_data(base_url, session, headers, now):
    """
    Scan order history (LAST_SOLD_LOOKBACK_DAYS) and return:
      recently_sold_ids — variant IDs sold within THRESHOLD_DAYS (excluded from report)
      last_sold_map     — { variant_id: most_recent_sale_datetime }
    """
    cutoff         = now - timedelta(days=THRESHOLD_DAYS)
    lookback_start = now - timedelta(days=LAST_SOLD_LOOKBACK_DAYS)

    safe_print(f"  Scanning orders (last {LAST_SOLD_LOOKBACK_DAYS} days)...")

    orders = paginate(
        f"{base_url}/orders.json",
        "orders",
        session,
        headers,
        params={
            "status":         "any",
            "limit":          250,
            "created_at_min": lookback_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields":         "id,created_at,line_items",
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


def get_last_adjustment_map(shop, session, headers, inventory_item_ids, now):
    """
    Batched GraphQL: most recent qualifying inventory adjustment per item.

    Qualifying criteria (all three must be true):
      1. reason == "correction"
      2. staffMember or app is present
      3. |sum(change.delta)| >= MIN_ADJUSTMENT_QUANTITY

    Returns { inventory_item_id (int): { date, days, actor, delta, qty_before, qty_after } }
    """
    graphql_url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    last_adj    = {}
    total       = len(inventory_item_ids)

    safe_print(
        f"  Querying adjustment history "
        f"({total:,} items, {GRAPHQL_BATCH_SIZE} per request)..."
    )

    for batch_start in range(0, total, GRAPHQL_BATCH_SIZE):
        batch = inventory_item_ids[batch_start: batch_start + GRAPHQL_BATCH_SIZE]
        gids  = [f"gid://shopify/InventoryItem/{iid}" for iid in batch]

        data = graphql_request(session, graphql_url, headers, ADJ_BATCH_QUERY, {"ids": gids})

        for node in data.get("data", {}).get("nodes", []):
            if not node or "inventoryAdjustmentGroups" not in node:
                continue

            inv_item_id = int(node["id"].split("/")[-1])
            best_dt     = None
            best_record = None

            for edge in node["inventoryAdjustmentGroups"].get("edges", []):
                n = edge.get("node", {})

                if n.get("reason") != "correction":
                    continue
                if not n.get("staffMember") and not n.get("app"):
                    continue

                changes   = n.get("changes", [])
                delta     = sum(c.get("delta", 0) for c in changes)
                qty_after = sum(c.get("quantityAfterChange", 0) for c in changes)

                if abs(delta) < MIN_ADJUSTMENT_QUANTITY:
                    continue

                created_at_str = n.get("createdAt")
                if not created_at_str:
                    continue
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))

                if best_dt is None or dt > best_dt:
                    best_dt = dt
                    staff_node = n.get("staffMember") or {}
                    app_node   = n.get("app") or {}
                    actor = (
                        staff_node.get("displayName")
                        or app_node.get("title")
                        or "Unknown"
                    )
                    best_record = {
                        "date":      dt.strftime("%Y-%m-%d"),
                        "days":      (now - dt).days,
                        "actor":     actor,
                        "delta":     delta,
                        "qty_after": qty_after,
                        "qty_before": qty_after - delta,
                    }

            if best_record:
                last_adj[inv_item_id] = best_record

        processed = min(batch_start + GRAPHQL_BATCH_SIZE, total)
        if processed % (GRAPHQL_BATCH_SIZE * 10) == 0 or processed >= total:
            safe_print(f"    Progress: {processed:,}/{total:,}")

    safe_print(f"  Found adjustment records for {len(last_adj):,}/{total:,} items.")
    return last_adj


# ─── PHASE 1 ORCHESTRATOR ────────────────────────────────────────────────────

def fetch_store_data(store, display_name, now):
    """
    Phase 1: fetch all data for one store.
    Returns a structured dict, or None if products/orders fetch fails entirely.
    If adjustment fetch fails, last_adj_map is None and columns 8-13 show 'No Record'.
    """
    shop     = store["shop"]
    token    = store["token"]
    base_url = f"https://{shop}/admin/api/{API_VERSION}"
    headers  = {
        "X-Shopify-Access-Token": token,
        "Content-Type":           "application/json",
    }
    session = make_session()

    safe_print(f"\n{'='*60}")
    safe_print(f"  Store: {shop}  ({display_name})")
    safe_print(f"{'='*60}")

    try:
        safe_print("  Fetching active products...")
        variants = get_all_variants(base_url, session, headers)
        safe_print(f"  Found {len(variants):,} active variants.")

        recently_sold_ids, last_sold_map = get_order_data(base_url, session, headers, now)

    except Exception as e:
        safe_print(f"  ✗ Critical error for {display_name}: {e}")
        return None

    inventory_item_ids = [v["inventory_item_id"] for v in variants if v["inventory_item_id"]]

    try:
        last_adj_map = get_last_adjustment_map(shop, session, headers, inventory_item_ids, now)
    except Exception as e:
        safe_print(f"  ⚠ Adjustment history failed for {display_name}: {e}")
        safe_print(f"    Columns 8–13 will show 'No Record' for this store.")
        last_adj_map = None

    return {
        "name":              display_name,
        "shop":              shop,
        "variants":          variants,
        "recently_sold_ids": recently_sold_ids,
        "last_sold_map":     last_sold_map,
        "last_adj_map":      last_adj_map,
    }


def main():
    pass


if __name__ == "__main__":
    main()
