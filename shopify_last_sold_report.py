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


def main():
    pass


if __name__ == "__main__":
    main()
