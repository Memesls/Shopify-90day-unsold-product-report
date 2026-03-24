# Shopify 90-Day Unsold Inventory Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `shopify_last_sold_report.py` as a two-phase pipeline that produces a formatted `.xlsx` workbook (one sheet per store) and per-store CSV files, adding shared inventory detection, full inventory adjustment detail columns, and a refund restock filter.

**Architecture:** Phase 1 fetches active products, order history, and inventory adjustment history from all stores in parallel threads. Phase 2 cross-references SKUs across stores for shared inventory detection, builds per-store report rows, then writes a single `.xlsx` workbook and individual CSVs.

**Tech Stack:** Python 3.8+, `requests` (existing), `openpyxl` (new), Shopify REST Admin API v2025-01, Shopify GraphQL Admin API (undocumented `inventoryAdjustmentGroups`), `pytest` + `unittest.mock` for testing.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `shopify_last_sold_report.py` | Full rewrite | All config, API helpers, Phase 1/2 pipeline, output writers, entry point |
| `tests/__init__.py` | Create | Makes `tests/` a package for pytest discovery |
| `tests/test_shopify_report.py` | Create | Unit tests for all pure-logic and data-shaping functions |

**Reference spec:** `docs/superpowers/specs/2026-03-23-shopify-90-day-unsold-report-design.md`

---

### Task 1: Setup — install dependency, initialize git, create test scaffold

**Files:**
- Modify: `shopify_last_sold_report.py` (replace with clean skeleton)
- Create: `tests/__init__.py`
- Create: `tests/test_shopify_report.py`

- [ ] **Step 1: Install openpyxl**

```bash
pip install openpyxl
```

Expected: installs without errors. Verify: `python -c "import openpyxl; print(openpyxl.__version__)"`.

- [ ] **Step 2: Initialize git repo if not already done**

From `D:\Claude Projects\Knocking 90-day unsold inventory\`:

```bash
git init
git add .
git commit -m "chore: initial commit — existing v2 script and docs"
```

If a repo already exists, skip this step.

- [ ] **Step 3: Create `tests/__init__.py`**

Create an empty file at `tests/__init__.py`.

- [ ] **Step 4: Create test file skeleton**

Create `tests/test_shopify_report.py`:

```python
"""
Unit tests for shopify_last_sold_report.py

Run all: pytest tests/test_shopify_report.py -v
"""
import sys
import os
import csv
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import shopify_last_sold_report as rpt
```

- [ ] **Step 5: Replace the script with an importable skeleton**

Replace all content in `shopify_last_sold_report.py` with:

```python
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


def main():
    pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify test file imports cleanly**

```bash
pytest tests/test_shopify_report.py -v
```

Expected: `0 tests collected`, no import errors.

- [ ] **Step 7: Commit**

```bash
git add shopify_last_sold_report.py tests/__init__.py tests/test_shopify_report.py
git commit -m "chore: add test scaffold, openpyxl dependency, and clean script skeleton"
```

---

### Task 2: Configuration block and store name deduplication

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_shopify_report.py`:

```python
class TestAssignStoreNames:
    def test_unique_names_unchanged(self):
        stores = [{"name": "CBSD"}, {"name": "LOSAD"}]
        assert rpt.assign_store_names(stores) == {0: "CBSD", 1: "LOSAD"}

    def test_duplicate_gets_suffix(self):
        stores = [{"name": "CBSD"}, {"name": "CBSD"}]
        assert rpt.assign_store_names(stores) == {0: "CBSD", 1: "CBSD_2"}

    def test_triple_duplicate(self):
        stores = [{"name": "X"}, {"name": "X"}, {"name": "X"}]
        assert rpt.assign_store_names(stores) == {0: "X", 1: "X_2", 2: "X_3"}

    def test_skips_suffix_already_taken_by_another_store(self):
        # "X_2" is already an explicit store name — third "X" must become "X_3"
        stores = [{"name": "X"}, {"name": "X_2"}, {"name": "X"}]
        assert rpt.assign_store_names(stores) == {0: "X", 1: "X_2", 2: "X_3"}
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestAssignStoreNames -v
```

Expected: `AttributeError: module has no attribute 'assign_store_names'`.

- [ ] **Step 3: Add configuration block and `assign_store_names` to script**

Add after the imports block in `shopify_last_sold_report.py`:

```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestAssignStoreNames -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add configuration block, FIELDNAMES constant, and assign_store_names"
```

---

### Task 3: Utility functions — HTTP helpers

**Files:**
- Modify: `shopify_last_sold_report.py`

These are carried over from v2 with light cleanup. No separate unit tests — the retry logic is thin and covered by integration behavior.

- [ ] **Step 1: Add `safe_print`, `make_session`, `api_request`, `paginate`**

Add after `assign_store_names` in `shopify_last_sold_report.py`:

```python
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
      - 429 (rate limit)  — honours Retry-After header
      - 5xx (server error) — exponential backoff
      - ConnectionError   — transient network issue
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
```

- [ ] **Step 2: Run test suite to confirm nothing broken**

```bash
pytest tests/test_shopify_report.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add shopify_last_sold_report.py
git commit -m "feat: add HTTP utility functions (safe_print, make_session, api_request, paginate)"
```

---

### Task 4: `graphql_request` utility

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestGraphqlRequest:
    def test_returns_data_on_success(self):
        response = MagicMock()
        response.json.return_value = {"data": {"nodes": []}}
        with patch.object(rpt, "api_request", return_value=response):
            result = rpt.graphql_request(MagicMock(), "https://x/graphql.json", {}, "query {}", {})
        assert result == {"data": {"nodes": []}}

    def test_retries_on_throttled_then_succeeds(self):
        throttled = MagicMock()
        throttled.json.return_value = {
            "errors": [{"extensions": {"code": "THROTTLED"}}]
        }
        success = MagicMock()
        success.json.return_value = {"data": {}}
        with patch.object(rpt, "api_request", side_effect=[throttled, success]):
            with patch("time.sleep"):
                result = rpt.graphql_request(MagicMock(), "https://x/graphql.json", {}, "query {}", {})
        assert result == {"data": {}}

    def test_raises_on_non_throttle_error(self):
        response = MagicMock()
        response.json.return_value = {"errors": [{"message": "Not found"}]}
        with patch.object(rpt, "api_request", return_value=response):
            with pytest.raises(Exception, match="GraphQL errors"):
                rpt.graphql_request(MagicMock(), "https://x/graphql.json", {}, "query {}", {})
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestGraphqlRequest -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `graphql_request`**

Add after `paginate` in `shopify_last_sold_report.py`:

```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestGraphqlRequest -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add graphql_request with THROTTLED retry and proactive cost-bucket slowdown"
```

---

### Task 5: `get_all_variants`

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestGetAllVariants:
    def test_returns_flat_variant_list_with_product_context(self):
        products = [{
            "id": 1, "title": "Shirt", "vendor": "Nike",
            "created_at": "2024-01-15T10:00:00Z",
            "variants": [
                {"id": 10, "title": "Blue / S", "sku": "SHIRT-B-S",
                 "inventory_item_id": 100, "inventory_quantity": 5},
            ],
        }]
        with patch.object(rpt, "paginate", return_value=products):
            result = rpt.get_all_variants("https://store/admin/api/2025-01", MagicMock(), {})
        assert len(result) == 1
        v = result[0]
        assert v["product_title"] == "Shirt"
        assert v["sku"] == "SHIRT-B-S"
        assert v["vendor"] == "Nike"
        assert v["inventory_quantity"] == 5
        assert isinstance(v["product_created_at"], datetime)

    def test_passes_status_active_param(self):
        with patch.object(rpt, "paginate", return_value=[]) as mock_pag:
            rpt.get_all_variants("https://store/admin/api/2025-01", MagicMock(), {})
        assert mock_pag.call_args[1]["params"]["status"] == "active"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestGetAllVariants -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `get_all_variants`**

Add after `graphql_request` in `shopify_last_sold_report.py`:

```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestGetAllVariants -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add get_all_variants with status=active filter and product created_at"
```

---

### Task 6: `get_order_data`

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestGetOrderData:
    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def test_recently_sold_ids_excludes_orders_over_threshold(self):
        now = self._now()
        old    = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = [
            {"created_at": old,    "line_items": [{"variant_id": 1}]},
            {"created_at": recent, "line_items": [{"variant_id": 2}]},
        ]
        with patch.object(rpt, "paginate", return_value=orders):
            recently_sold, _ = rpt.get_order_data("https://store/admin/api/2025-01", MagicMock(), {}, now)
        assert 2 in recently_sold
        assert 1 not in recently_sold

    def test_last_sold_map_keeps_most_recent_order_date(self):
        now = self._now()
        date1 = (now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        date2 = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = [
            {"created_at": date1, "line_items": [{"variant_id": 5}]},
            {"created_at": date2, "line_items": [{"variant_id": 5}]},
        ]
        with patch.object(rpt, "paginate", return_value=orders):
            _, last_sold = rpt.get_order_data("https://store/admin/api/2025-01", MagicMock(), {}, now)
        assert (now - last_sold[5]).days == 100
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestGetOrderData -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `get_order_data`**

```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestGetOrderData -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add get_order_data (accepts now param for testability)"
```

---

### Task 7: `get_last_adjustment_map`

Most complex function — updated GraphQL query with `changes`, three qualifying criteria, and enriched return dict. Pay close attention to the delta threshold filter.

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Add the GraphQL query constant**

Add after the `paginate` / `graphql_request` block:

```python
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
```

- [ ] **Step 2: Write failing tests**

```python
class TestGetLastAdjustmentMap:
    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def _node(self, inv_id, edges):
        return {
            "id": f"gid://shopify/InventoryItem/{inv_id}",
            "inventoryAdjustmentGroups": {"edges": edges},
        }

    def _edge(self, created_at, reason, staff=None, app=None, delta=10, qty_after=100):
        return {"node": {
            "createdAt": created_at,
            "reason": reason,
            "staffMember": {"displayName": staff} if staff else None,
            "app": {"title": app} if app else None,
            "changes": [{"delta": delta, "quantityAfterChange": qty_after}],
        }}

    def _fake_data(self, *nodes):
        return {"data": {"nodes": list(nodes)}}

    def test_qualifies_staff_correction(self):
        node = self._node(42, [self._edge("2026-01-01T10:00:00Z", "correction", staff="John", delta=50, qty_after=200)])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store.myshopify.com", MagicMock(), {}, [42], self._now())
        adj = result[42]
        assert adj["actor"] == "John"
        assert adj["delta"] == 50
        assert adj["qty_after"] == 200
        assert adj["qty_before"] == 150

    def test_qualifies_app_correction(self):
        node = self._node(99, [self._edge("2026-01-01T10:00:00Z", "correction", app="Knockify-2.2", delta=20, qty_after=80)])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store", MagicMock(), {}, [99], self._now())
        assert result[99]["actor"] == "Knockify-2.2"

    def test_excludes_wrong_reason(self):
        node = self._node(1, [self._edge("2026-01-01T10:00:00Z", "restock", staff="Jane", delta=10)])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store", MagicMock(), {}, [1], self._now())
        assert 1 not in result

    def test_excludes_no_actor(self):
        node = self._node(2, [self._edge("2026-01-01T10:00:00Z", "correction", delta=10)])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store", MagicMock(), {}, [2], self._now())
        assert 2 not in result

    def test_excludes_delta_below_threshold(self):
        # delta=2, MIN_ADJUSTMENT_QUANTITY=5 — should not qualify
        node = self._node(3, [self._edge("2026-01-01T10:00:00Z", "correction", staff="Ana", delta=2, qty_after=10)])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store", MagicMock(), {}, [3], self._now())
        assert 3 not in result

    def test_picks_most_recent_qualifying_record(self):
        node = self._node(5, [
            self._edge("2025-06-01T10:00:00Z", "correction", staff="Old", delta=10, qty_after=100),
            self._edge("2026-02-01T10:00:00Z", "correction", staff="New", delta=15, qty_after=200),
        ])
        with patch.object(rpt, "graphql_request", return_value=self._fake_data(node)):
            result = rpt.get_last_adjustment_map("store", MagicMock(), {}, [5], self._now())
        assert result[5]["actor"] == "New"
        assert result[5]["delta"] == 15
```

- [ ] **Step 3: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestGetLastAdjustmentMap -v
```

Expected: `AttributeError`.

- [ ] **Step 4: Implement `get_last_adjustment_map`**

```python
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
```

- [ ] **Step 5: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestGetLastAdjustmentMap -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add get_last_adjustment_map with delta/qty columns and MIN_ADJUSTMENT_QUANTITY filter"
```

---

### Task 8: `fetch_store_data` — Phase 1 orchestrator

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestFetchStoreData:
    def _store(self):
        return {"shop": "test.myshopify.com", "token": "tok", "name": "TEST"}

    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def test_returns_structured_dict_on_success(self):
        variants = [{"inventory_item_id": 1, "sku": "A"}]
        adj_map  = {1: {"date": "2026-01-01", "days": 10, "actor": "John",
                        "delta": 10, "qty_before": 90, "qty_after": 100}}
        with patch.object(rpt, "make_session"), \
             patch.object(rpt, "get_all_variants", return_value=variants), \
             patch.object(rpt, "get_order_data", return_value=(set(), {})), \
             patch.object(rpt, "get_last_adjustment_map", return_value=adj_map):
            result = rpt.fetch_store_data(self._store(), "MYSTORE", self._now())
        assert result["name"] == "MYSTORE"
        assert result["variants"] == variants
        assert result["last_adj_map"] == adj_map

    def test_returns_none_when_product_fetch_fails(self):
        with patch.object(rpt, "make_session"), \
             patch.object(rpt, "get_all_variants", side_effect=Exception("API down")):
            result = rpt.fetch_store_data(self._store(), "MYSTORE", self._now())
        assert result is None

    def test_sets_adj_map_to_none_when_graphql_fails(self):
        with patch.object(rpt, "make_session"), \
             patch.object(rpt, "get_all_variants", return_value=[{"inventory_item_id": 1}]), \
             patch.object(rpt, "get_order_data", return_value=(set(), {})), \
             patch.object(rpt, "get_last_adjustment_map", side_effect=Exception("GQL error")):
            result = rpt.fetch_store_data(self._store(), "MYSTORE", self._now())
        assert result is not None
        assert result["last_adj_map"] is None
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestFetchStoreData -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `fetch_store_data`**

```python
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
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestFetchStoreData -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add fetch_store_data Phase 1 orchestrator with graceful adjustment fallback"
```

---

### Task 9: `build_shared_sku_map`

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestBuildSharedSkuMap:
    def _store(self, name, skus):
        return {"name": name, "variants": [{"sku": s} for s in skus]}

    def test_shared_sku_lists_both_stores(self):
        result = rpt.build_shared_sku_map([
            self._store("CBSD",  ["SKU-A", "SKU-B"]),
            self._store("LOSAD", ["SKU-A", "SKU-C"]),
        ])
        assert set(result["SKU-A"]) == {"CBSD", "LOSAD"}

    def test_unique_sku_has_single_store(self):
        result = rpt.build_shared_sku_map([
            self._store("CBSD",  ["SKU-A"]),
            self._store("LOSAD", ["SKU-B"]),
        ])
        assert result["SKU-A"] == ["CBSD"]
        assert result["SKU-B"] == ["LOSAD"]

    def test_blank_sku_excluded(self):
        result = rpt.build_shared_sku_map([
            self._store("CBSD",  [""]),
            self._store("LOSAD", [""]),
        ])
        assert "" not in result
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestBuildSharedSkuMap -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `build_shared_sku_map`**

```python
# ─── PHASE 2 ─────────────────────────────────────────────────────────────────

def build_shared_sku_map(all_store_data):
    """
    Cross-reference active SKUs across all successfully-fetched stores.
    Returns { sku: [store_names] } — only non-blank SKUs included.
    """
    sku_map = defaultdict(list)
    for store_data in all_store_data:
        name = store_data["name"]
        for v in store_data["variants"]:
            sku = v.get("sku", "")
            if sku:
                sku_map[sku].append(name)
    return dict(sku_map)
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestBuildSharedSkuMap -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add build_shared_sku_map for cross-store SKU detection"
```

---

### Task 10: `build_report_rows`

Core business logic. Applies all filters, computes all 14 columns, sorts by days descending.

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestBuildReportRows:
    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def _variant(self, **kw):
        base = {
            "variant_id": 1, "product_title": "T-Shirt",
            "product_created_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "variant_title": "Blue / M", "sku": "TS-B-M",
            "vendor": "Nike", "inventory_quantity": 10, "inventory_item_id": 100,
        }
        base.update(kw)
        return base

    def _store(self, variants, recently_sold=None, last_sold=None, adj_map=None, name="CBSD"):
        return {
            "name": name, "variants": variants,
            "recently_sold_ids": recently_sold or set(),
            "last_sold_map": last_sold or {},
            "last_adj_map": adj_map,
        }

    def test_excludes_zero_inventory(self):
        rows = rpt.build_report_rows(self._store([self._variant(inventory_quantity=0)]), {}, self._now())
        assert len(rows) == 0

    def test_excludes_recently_sold(self):
        v = self._variant()
        rows = rpt.build_report_rows(self._store([v], recently_sold={v["variant_id"]}), {}, self._now())
        assert len(rows) == 0

    def test_includes_unsold_with_stock(self):
        rows = rpt.build_report_rows(self._store([self._variant()]), {}, self._now())
        assert len(rows) == 1

    def test_days_since_sale_uses_last_sold_date(self):
        now = self._now()
        v = self._variant()
        last_sold = now - timedelta(days=120)
        rows = rpt.build_report_rows(self._store([v], last_sold={v["variant_id"]: last_sold}), {}, now)
        assert rows[0]["Days Since Last Sale"] == 120
        assert rows[0]["Last Sold Date"] == last_sold.strftime("%Y-%m-%d")

    def test_never_sold_falls_back_to_product_created_at(self):
        now = self._now()
        created = datetime(2023, 1, 1, tzinfo=timezone.utc)
        v = self._variant(product_created_at=created)
        rows = rpt.build_report_rows(self._store([v]), {}, now)
        assert rows[0]["Last Sold Date"] == "Never Sold"
        assert rows[0]["Days Since Last Sale"] == (now - created).days

    def test_adjustment_columns_populated_from_adj_map(self):
        v = self._variant()
        adj = {"date": "2026-01-01", "days": 83, "actor": "John",
               "delta": 50, "qty_before": 150, "qty_after": 200}
        rows = rpt.build_report_rows(self._store([v], adj_map={v["inventory_item_id"]: adj}), {}, self._now())
        r = rows[0]
        assert r["Last Inventory Adjustment"] == "2026-01-01"
        assert r["Days Since Last Adjustment"] == 83
        assert r["Adjusted By"] == "John"
        assert r["Previous Inventory"] == 150
        assert r["Adjustment Quantity"] == 50
        assert r["New Inventory"] == 200

    def test_no_adjustment_record_shows_no_record(self):
        rows = rpt.build_report_rows(self._store([self._variant()], adj_map={}), {}, self._now())
        r = rows[0]
        assert r["Last Inventory Adjustment"] == "No Record"
        assert r["Days Since Last Adjustment"] == "No Record"
        assert r["Adjusted By"] == "No Record"
        assert r["Previous Inventory"] == "No Record"
        assert r["Adjustment Quantity"] == "No Record"
        assert r["New Inventory"] == "No Record"

    def test_adj_map_none_also_shows_no_record(self):
        rows = rpt.build_report_rows(self._store([self._variant()], adj_map=None), {}, self._now())
        assert rows[0]["Adjusted By"] == "No Record"

    def test_shared_inventory_lists_other_stores(self):
        v = self._variant(sku="SHARED")
        rows = rpt.build_report_rows(
            self._store([v], name="CBSD"),
            {"SHARED": ["CBSD", "LOSAD"]},
            self._now()
        )
        assert rows[0]["Shared Inventory"] == "Shared with: LOSAD"

    def test_unique_sku_shows_dash(self):
        v = self._variant(sku="UNIQUE")
        rows = rpt.build_report_rows(
            self._store([v], name="CBSD"),
            {"UNIQUE": ["CBSD"]},
            self._now()
        )
        assert rows[0]["Shared Inventory"] == "—"

    def test_sorted_descending_by_days_since_sale(self):
        now = self._now()
        v1 = self._variant(variant_id=1, product_created_at=now - timedelta(days=200))
        v2 = self._variant(variant_id=2, product_created_at=now - timedelta(days=100))
        rows = rpt.build_report_rows(self._store([v1, v2]), {}, now)
        assert rows[0]["Days Since Last Sale"] > rows[1]["Days Since Last Sale"]
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestBuildReportRows -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `build_report_rows`**

```python
def build_report_rows(store_data, shared_sku_map, now):
    """
    Apply filters and build report rows for one store.
    Returns list of dicts keyed by FIELDNAMES, sorted descending by Days Since Last Sale.
    """
    store_name        = store_data["name"]
    recently_sold_ids = store_data["recently_sold_ids"]
    last_sold_map     = store_data["last_sold_map"]
    last_adj_map      = store_data["last_adj_map"]  # may be None

    rows = []
    for v in store_data["variants"]:
        if v["inventory_quantity"] <= 0:
            continue
        if v["variant_id"] in recently_sold_ids:
            continue

        # ── Last sold ─────────────────────────────────────────────────────────
        vid = v["variant_id"]
        if vid in last_sold_map:
            last_sold_dt    = last_sold_map[vid]
            last_sold_str   = last_sold_dt.strftime("%Y-%m-%d")
            days_since_sale = (now - last_sold_dt).days
        else:
            last_sold_str   = "Never Sold"
            created_at      = v.get("product_created_at") or now
            days_since_sale = (now - created_at).days

        # ── Inventory adjustment ──────────────────────────────────────────────
        inv_id = v.get("inventory_item_id")
        adj    = (last_adj_map or {}).get(inv_id) if inv_id else None

        if adj:
            last_adj_date  = adj["date"]
            days_since_adj = adj["days"]
            adjusted_by    = adj["actor"]
            prev_inv       = adj["qty_before"]
            adj_qty        = adj["delta"]
            new_inv        = adj["qty_after"]
        else:
            last_adj_date  = "No Record"
            days_since_adj = "No Record"
            adjusted_by    = "No Record"
            prev_inv       = "No Record"
            adj_qty        = "No Record"
            new_inv        = "No Record"

        # ── Shared inventory ──────────────────────────────────────────────────
        sku = v.get("sku", "")
        other_stores = [
            n for n in shared_sku_map.get(sku, []) if n != store_name
        ] if sku else []
        shared_str = f"Shared with: {', '.join(other_stores)}" if other_stores else "—"

        rows.append({
            "Product Title":              v["product_title"],
            "Product Variant":            v["variant_title"],
            "SKU":                        sku,
            "Vendor":                     v["vendor"],
            "Available Inventory":        v["inventory_quantity"],
            "Last Sold Date":             last_sold_str,
            "Days Since Last Sale":       days_since_sale,
            "Last Inventory Adjustment":  last_adj_date,
            "Days Since Last Adjustment": days_since_adj,
            "Adjusted By":                adjusted_by,
            "Previous Inventory":         prev_inv,
            "Adjustment Quantity":        adj_qty,
            "New Inventory":              new_inv,
            "Shared Inventory":           shared_str,
        })

    rows.sort(key=lambda r: r["Days Since Last Sale"], reverse=True)
    return rows
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestBuildReportRows -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add build_report_rows with all 14 columns, filters, and sort"
```

---

### Task 11: `write_xlsx`

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestWriteXlsx:
    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def _row(self, last_sold="2025-01-01", shared="—"):
        r = {fn: f"val" for fn in rpt.FIELDNAMES}
        r["Last Sold Date"] = last_sold
        r["Shared Inventory"] = shared
        return r

    def test_creates_xlsx_file(self, tmp_path):
        with patch.object(rpt, "OUTPUT_XLSX_DIR", str(tmp_path)):
            path = rpt.write_xlsx({"CBSD": [self._row()]}, self._now())
        assert os.path.exists(path)
        assert path.endswith(".xlsx")

    def test_has_one_sheet_per_store(self, tmp_path):
        from openpyxl import load_workbook
        with patch.object(rpt, "OUTPUT_XLSX_DIR", str(tmp_path)):
            path = rpt.write_xlsx({"CBSD": [self._row()], "LOSAD": [self._row()]}, self._now())
        assert set(load_workbook(path).sheetnames) == {"CBSD", "LOSAD"}

    def test_header_matches_fieldnames(self, tmp_path):
        from openpyxl import load_workbook
        with patch.object(rpt, "OUTPUT_XLSX_DIR", str(tmp_path)):
            path = rpt.write_xlsx({"CBSD": [self._row()]}, self._now())
        ws = load_workbook(path)["CBSD"]
        assert [ws.cell(1, c).value for c in range(1, len(rpt.FIELDNAMES) + 1)] == rpt.FIELDNAMES

    def test_never_sold_row_has_red_fill(self, tmp_path):
        from openpyxl import load_workbook
        with patch.object(rpt, "OUTPUT_XLSX_DIR", str(tmp_path)):
            path = rpt.write_xlsx({"CBSD": [self._row(last_sold="Never Sold")]}, self._now())
        ws = load_workbook(path)["CBSD"]
        assert ws.cell(2, 1).fill.fgColor.rgb.endswith("FFD0D0")

    def test_shared_inventory_cell_has_yellow_fill(self, tmp_path):
        from openpyxl import load_workbook
        with patch.object(rpt, "OUTPUT_XLSX_DIR", str(tmp_path)):
            path = rpt.write_xlsx({"CBSD": [self._row(shared="Shared with: LOSAD")]}, self._now())
        ws = load_workbook(path)["CBSD"]
        shared_col = rpt.FIELDNAMES.index("Shared Inventory") + 1
        assert ws.cell(2, shared_col).fill.fgColor.rgb.endswith("FFF2CC")
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestWriteXlsx -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `write_xlsx`**

```python
# ─── OUTPUT WRITERS ───────────────────────────────────────────────────────────

def write_xlsx(all_store_rows, now):
    """
    Write a single .xlsx workbook with one sheet per store.
    Formatting: bold frozen header, red rows for never-sold, yellow shared-inventory cell.
    Returns the file path.
    """
    wb = Workbook()
    wb.remove(wb.active)  # remove default blank sheet

    RED_FILL    = PatternFill("solid", fgColor="FFD0D0")
    YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
    BOLD        = Font(bold=True)
    shared_col  = FIELDNAMES.index("Shared Inventory") + 1  # 1-based

    for store_name, rows in all_store_rows.items():
        ws = wb.create_sheet(title=store_name)

        ws.append(FIELDNAMES)
        for cell in ws[1]:
            cell.font = BOLD
        ws.freeze_panes = "A2"

        for row in rows:
            ws.append([row[fn] for fn in FIELDNAMES])
            row_idx = ws.max_row

            if row.get("Last Sold Date") == "Never Sold":
                for cell in ws[row_idx]:
                    cell.fill = RED_FILL

            if str(row.get("Shared Inventory", "")).startswith("Shared with:"):
                ws.cell(row=row_idx, column=shared_col).fill = YELLOW_FILL

        for col_idx, col_cells in enumerate(ws.columns, 1):
            max_len = max(
                len(str(cell.value)) if cell.value is not None else 0
                for cell in col_cells
            )
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len * 1.2, 60)

    os.makedirs(OUTPUT_XLSX_DIR, exist_ok=True)
    filename = os.path.join(OUTPUT_XLSX_DIR, f"report_{now.strftime('%Y%m%d')}.xlsx")
    wb.save(filename)
    return filename
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestWriteXlsx -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add write_xlsx with bold frozen header, red/yellow fills, auto-width columns"
```

---

### Task 12: `write_csv`

**Files:**
- Modify: `shopify_last_sold_report.py`
- Modify: `tests/test_shopify_report.py`

- [ ] **Step 1: Write failing tests**

```python
class TestWriteCsv:
    def _now(self):
        return datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)

    def test_creates_csv_with_correct_columns_and_data(self, tmp_path):
        row = {fn: f"val_{fn}" for fn in rpt.FIELDNAMES}
        with patch.object(rpt, "OUTPUT_CSV_DIR", str(tmp_path)):
            path = rpt.write_csv([row], "CBSD", self._now())
        assert os.path.exists(path)
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert list(rows[0].keys()) == rpt.FIELDNAMES
        assert rows[0]["SKU"] == "val_SKU"

    def test_filename_includes_store_name_and_date(self, tmp_path):
        with patch.object(rpt, "OUTPUT_CSV_DIR", str(tmp_path)):
            path = rpt.write_csv([], "CBSD", self._now())
        assert "CBSD" in os.path.basename(path)
        assert "20260324" in os.path.basename(path)
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/test_shopify_report.py::TestWriteCsv -v
```

Expected: `AttributeError`.

- [ ] **Step 3: Implement `write_csv`**

```python
def write_csv(rows, store_name, now):
    """Write one CSV file for a store (UTF-8 BOM for Excel compatibility). Returns file path."""
    os.makedirs(OUTPUT_CSV_DIR, exist_ok=True)
    filename = os.path.join(
        OUTPUT_CSV_DIR,
        f"report_{store_name}_{now.strftime('%Y%m%d')}.csv",
    )
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return filename
```

- [ ] **Step 4: Run test to confirm pass**

```bash
pytest tests/test_shopify_report.py::TestWriteCsv -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add shopify_last_sold_report.py tests/test_shopify_report.py
git commit -m "feat: add write_csv with UTF-8 BOM encoding"
```

---

### Task 13: `main` — entry point

No unit tests for `main` — it is a thin orchestrator over already-tested functions. Verify manually.

**Files:**
- Modify: `shopify_last_sold_report.py`

- [ ] **Step 1: Implement `main`**

Replace the stub `main()` in `shopify_last_sold_report.py` with:

```python
# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)

    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Shopify Last Sold Report Generator v3           ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  Threshold:               {THRESHOLD_DAYS} days")
    print(f"  Order lookback:          {LAST_SOLD_LOOKBACK_DAYS} days")
    print(f"  Min adjustment quantity: {MIN_ADJUSTMENT_QUANTITY} units")
    print(f"  GraphQL batch:           {GRAPHQL_BATCH_SIZE} items/request")
    print(f"  Stores:                  {len(STORES)}")
    print(f"  Parallel workers:        {min(STORE_WORKERS, len(STORES))}")

    store_names = assign_store_names(STORES)

    # ── Phase 1: parallel fetch ───────────────────────────────────────────────
    all_store_data = []
    errors         = []
    workers        = min(STORE_WORKERS, len(STORES))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_store_data, store, store_names[i], now): (i, store)
            for i, store in enumerate(STORES)
        }
        for future in as_completed(futures):
            i, store = futures[future]
            try:
                data = future.result()
                if data is not None:
                    all_store_data.append(data)
            except Exception as e:
                errors.append((store_names[i], str(e)))
                safe_print(f"\n  ✗ Unhandled error for {store_names[i]}: {e}")

    if not all_store_data:
        print("\n  No store data fetched. Exiting.")
        return

    # ── Phase 2: cross-reference + output ────────────────────────────────────
    shared_sku_map = build_shared_sku_map(all_store_data)

    all_store_rows = {}
    generated_csvs = []

    for store_data in all_store_data:
        name = store_data["name"]
        rows = build_report_rows(store_data, shared_sku_map, now)
        all_store_rows[name] = rows
        csv_path = write_csv(rows, name, now)
        generated_csvs.append(csv_path)
        safe_print(f"\n  ✓ CSV saved: {csv_path}  ({len(rows):,} rows)")

    xlsx_path = write_xlsx(all_store_rows, now)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Done. {len(all_store_data)} store(s) processed.")
    print(f"\n  Excel report:  {xlsx_path}")
    print(f"  CSV files:")
    for path in generated_csvs:
        print(f"    {path}")
    if errors:
        print(f"\n  {len(errors)} store(s) failed:")
        for name, err in errors:
            print(f"    {name}: {err}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/test_shopify_report.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Verify the script starts cleanly with placeholder config**

```bash
python shopify_last_sold_report.py
```

Expected: banner prints, then a connection error for the placeholder store — confirms no `ImportError` or `SyntaxError`.

- [ ] **Step 4: Commit**

```bash
git add shopify_last_sold_report.py
git commit -m "feat: add main() completing v3 two-phase pipeline"
```

---

### Task 14: End-to-end verification against a real store

No code changes — manual verification only.

- [ ] **Step 1: Update `STORES` with one real store's credentials**

In `shopify_last_sold_report.py`, fill in one real entry in `STORES`.

- [ ] **Step 2: Run against the real store**

```bash
python shopify_last_sold_report.py
```

Verify console output:
- Active variant count is non-zero
- Order scan reports a processed count
- Adjustment history shows progress
- "Done. 1 store(s) processed."
- Paths to `.xlsx` and `.csv` printed

- [ ] **Step 3: Open the `.xlsx` and verify**

Checks:
- [ ] Header row is bold and frozen (scroll down — row 1 stays visible)
- [ ] "Never Sold" rows have a red background
- [ ] Rows where column 14 shows "Shared with: ..." have a yellow cell in that column only
- [ ] Columns 11–13 (Previous Inventory, Adjustment Quantity, New Inventory) show integers for a known-adjusted item, or "No Record" for items with no qualifying adjustment
- [ ] Column 10 (Adjusted By) shows a staff name or "Knockify-2.2"
- [ ] Days Since Last Sale sorts highest first

- [ ] **Step 4: Add remaining stores and run the full multi-store report**

Fill in all stores in `STORES`. Run again. Verify:
- One sheet per store in the `.xlsx`
- One CSV per store in the CSV folder
- Products with the same SKU across stores show "Shared with: ..." in column 14

- [ ] **Step 5: Final commit**

```bash
git add shopify_last_sold_report.py
git commit -m "chore: v3 complete — verified against production stores"
```
