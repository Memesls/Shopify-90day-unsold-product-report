# Shopify 90-Day Unsold Inventory Report — Design Spec
**Date:** 2026-03-23
**Status:** Approved
**File:** `shopify_last_sold_report.py` (upgrade of existing v2 script)

---

## 1. Purpose

Generate a report identifying active Shopify product variants that:
- Have **not sold in over 90 days**
- Are **currently in stock** (inventory > 0)
- Have **active** product status (not draft or archived)

Output is used to manually zero out dead stock across multiple Shopify stores.

---

## 2. Stores & Configuration

The script manages **4+ Shopify stores**. Each store entry in the `STORES` config list carries three fields:

```python
STORES = [
    {"shop": "store-one.myshopify.com", "token": "token_one", "name": "CBSD"},
    {"shop": "store-two.myshopify.com", "token": "token_two", "name": "LOSAD"},
    # ...
]
```

The `name` field drives:
- `.xlsx` sheet tab name
- CSV filename (e.g. `report_CBSD_20260323.csv`)
- "Shared with: ..." text in the Shared Inventory column

**Store names must be unique.** If two or more stores share the same `name`, the first occurrence retains the bare name (no suffix). Subsequent duplicates are suffixed with an incrementing counter (`_2`, `_3`, etc.). The counter keeps incrementing until a name that does not appear anywhere in the already-assigned set is found — e.g., if "CBSD" and "CBSD_2" are both already taken (by config or prior assignment), the next duplicate becomes "CBSD_3". The assigned name (including any suffix) is used consistently everywhere: sheet tab, CSV filename, and "Shared with: ..." text in other stores' rows.

### Tunable constants

| Constant | Default | Purpose |
|----------|---------|---------|
| `THRESHOLD_DAYS` | `90` | Unsold beyond this → appears in report |
| `LAST_SOLD_LOOKBACK_DAYS` | `365` | Order history scan window for `last_sold_map` |
| `GRAPHQL_BATCH_SIZE` | `25` | Inventory items per GraphQL `nodes()` request |
| `MIN_ADJUSTMENT_QUANTITY` | `5` | Minimum absolute inventory delta for a correction to qualify. Adjustments with `|delta| < 5` are excluded — this filters out incidental restock events from refunds/replacements (typically +1 or +2 units). Tune upward if false positives persist. |
| `STORE_WORKERS` | `4` | Max parallel store threads (capped to `len(STORES)`) |
| `MAX_RETRIES` | `6` | Retries on rate limits / transient errors |
| `API_VERSION` | `"2025-01"` | Shopify API version |
| `OUTPUT_XLSX_DIR` | `D:\Claude Projects\Knocking 90-day unsold inventory` | Single .xlsx output location |
| `OUTPUT_CSV_DIR` | `D:\Claude Projects\Knocking 90-day unsold inventory\CSV files` | Per-store CSV output location |

---

## 3. Architecture — Two-Phase Pipeline

Execution is split into two phases to support cross-store shared inventory detection.

### Phase 1 — Parallel Fetch

All stores are fetched simultaneously using `ThreadPoolExecutor(max_workers=min(STORE_WORKERS, len(STORES)))`.

Each store fetch (via `fetch_store_data()`) collects:
1. **Active product variants** — REST `/products.json?status=active&limit=250`, paginated via Link headers. Fields fetched: `id, title, vendor, created_at, variants`. Each variant carries `id, title, sku, inventory_item_id, inventory_quantity`.
2. **Order history** — REST `/orders.json?status=any&limit=250&created_at_min=<lookback_start>`, paginated. Scans back `LAST_SOLD_LOOKBACK_DAYS` days and builds two structures:
   - `last_sold_map`: `{ variant_id (int) → most_recent_sale_datetime }` — built by iterating `order["line_items"]` and reading `line_item["variant_id"]`. The sale datetime is taken from `order["created_at"]`. The most recent `order["created_at"]` per variant across all scanned orders is kept.
   - `recently_sold_ids`: `set` of variant IDs where `most_recent_sale_datetime >= now - THRESHOLD_DAYS`
3. **Inventory adjustment history** — Batched GraphQL using `inventoryAdjustmentGroups` (see Section 7).

**Failure handling — granularity:**
- If `get_all_variants()` or `get_order_data()` raises an unrecoverable exception for a store, the **entire store is skipped** (no rows, no sheet, no CSV). The error is printed and included in the final summary.
- If `get_last_adjustment_map()` fails for a store, **that store's rows are still generated** but columns 8–13 (Last Inventory Adjustment, Days Since Last Adjustment, Adjusted By, Previous Inventory, Adjustment Quantity, New Inventory) all show `"No Record"`. This is noted in the console output.

### Phase 2 — Cross-Reference & Output

Runs sequentially after all Phase 1 fetches complete.

1. **`build_shared_sku_map(all_store_data)`** — builds `{ sku → [store_names] }` from active variants across all successfully-fetched stores.
2. **`build_report_rows(store_data, shared_sku_map)`** — applies filters, computes all columns, returns sorted row list.
3. **`write_xlsx(all_store_rows)`** — writes a single `.xlsx` workbook, one sheet per store. The report date (used in the filename) is the UTC datetime captured **once at script start** in `main()` and passed through — not re-evaluated at write time.
4. **`write_csv(rows, store_name)`** — writes one CSV per store.

---

## 4. Filtering Logic

A variant is **included** in the report if and only if:
- Product `status == "active"` *(enforced at the Shopify API level via `status=active` — draft and archived products are never fetched)*
- `inventory_quantity > 0` *(negative inventory, which Shopify allows when "allow overselling" is enabled, is excluded by this same condition — no separate handling required)*
- Variant ID **not** in `recently_sold_ids`

**Order status:** The order scan uses `status=any` (all orders, including cancelled and refunded). This is intentional and matches the existing v2 behavior. A cancelled order still represents a demand signal and is conservatively treated as a sale for the purpose of the "last sold" calculation. This is an accepted trade-off — the goal of the report is to identify truly dormant inventory, and erring toward *including* more recent activity means fewer false positives in the report.

---

## 5. Column Specification

Columns appear left to right in this order:

| # | Column Name | Value |
|---|-------------|-------|
| 1 | **Product Title** | Product name |
| 2 | **Product Variant** | Variant title (e.g. "Blue / XL") |
| 3 | **SKU** | Variant SKU |
| 4 | **Vendor** | Product vendor |
| 5 | **Available Inventory** | `inventory_quantity` from the REST variants endpoint. See known limitation in Section 13. |
| 6 | **Last Sold Date** | `YYYY-MM-DD` of most recent sale within `LAST_SOLD_LOOKBACK_DAYS`. `"Never Sold"` if no sale found in the window. See known limitation in Section 13 regarding items sold outside the window. |
| 7 | **Days Since Last Sale** | Integer days since last sale. If no sale found in the `LAST_SOLD_LOOKBACK_DAYS` window (whether the item was truly never sold, or was last sold before the window), falls back to days since the product's `created_at` date. This fallback applies uniformly — no distinction is made between "truly never sold" and "sold outside the lookback window." This is intentional: both cases are treated as unknown/stale and ranked accordingly. |
| 8 | **Last Inventory Adjustment** | `YYYY-MM-DD` of most recent qualifying manual correction, or `"No Record"` |
| 9 | **Days Since Last Adjustment** | Integer days since last adjustment, written as an **Excel numeric cell** (not a string) so Excel sorting works correctly. `"No Record"` is written as a string cell in all cases — both when no qualifying adjustment exists for a variant, and when `get_last_adjustment_map()` fails entirely for a store (see Section 3 failure handling). The script-applied sort is by column 7 only. If a user manually sorts by column 9 in Excel, "No Record" rows will sort separately from numeric rows — this is a known display limitation and no special handling is added. |
| 10 | **Adjusted By** | `staffMember.displayName` if present, else `app.title` (e.g. `"Knockify-2.2"`), else `"No Record"` |
| 11 | **Previous Inventory** | Stock quantity immediately before the qualifying adjustment. Computed as `sum(quantityAfterChange) − sum(delta)` across all location changes in the adjustment group. Plain integer, written as Excel numeric cell. `"No Record"` if no qualifying adjustment found. |
| 12 | **Adjustment Quantity** | The net inventory change from the qualifying adjustment. Computed as `sum(delta)` across all location changes. Plain integer (positive = stock added, negative = stock removed), written as Excel numeric cell. `"No Record"` if no qualifying adjustment found. |
| 13 | **New Inventory** | Stock quantity immediately after the qualifying adjustment. Computed as `sum(quantityAfterChange)` across all location changes. Plain integer, written as Excel numeric cell. `"No Record"` if no qualifying adjustment found. |
| 14 | **Shared Inventory** | `"—"` if SKU is unique to this store. `"Shared with: NAME1, NAME2"` listing other stores' `name` values where the same SKU is active. |

**Sort order:** Descending by `Days Since Last Sale`. Because never-sold / outside-window variants fall back to `created_at`, they will typically rank above variants that recently crossed the 90-day threshold. "Never Sold" label in column 6 is a known UX ambiguity (see Section 13) — no separate display path is added for "sold outside window" vs. "truly never sold."

---

## 6. Shared Inventory Detection

- Two variants are "shared" if their SKU strings match exactly (case-sensitive).
- Only active variants from successfully-fetched stores are included in the cross-reference. If a store's Phase 1 fetch fails entirely, its SKUs are absent from the map — rows on other stores that would have matched will show `"—"` instead of `"Shared with: ..."`. This is a silent data quality trade-off documented in Section 13.
- Variants with a blank or empty SKU (`""`) are excluded from shared detection to avoid false positives.
- `build_shared_sku_map()` runs after Phase 1 completes. For each store/SKU pair it records the store's `name`. The resulting map is `{ sku: [list of store names where this SKU is active] }`.
- When building a store's rows, the store's own name is excluded from the "Shared with: ..." list.

---

## 7. Inventory Adjustment Logic

### GraphQL query

Uses the `inventoryAdjustmentGroups` field on `InventoryItem` nodes, batched via the `nodes(ids: [ID!]!)` root query — the same approach as the existing v2 script, confirmed working against API version `2025-01`. **This field is not part of Shopify's publicly documented GraphQL schema** and may be subject to change across API versions or restricted on certain store plans. If the field returns `null` or is absent for a node, that inventory item is treated as having no qualifying adjustment (`"No Record"` for columns 8–10).

This is **not** cursor-paginated. Up to `GRAPHQL_BATCH_SIZE` inventory item global IDs are passed per request via the `nodes()` lookup. Bare integer `inventory_item_id` values from the REST response must be converted to Shopify global IDs before use: `f"gid://shopify/InventoryItem/{inventory_item_id}"`.

```graphql
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
```

`last: 50` fetches the 50 most recent adjustments per item. `changes` is a list of per-location quantity changes within the adjustment group.

### Qualifying criteria

An adjustment record qualifies if **all** of the following are true:
- `reason == "correction"` — covers both "Manually adjusted" and "Inventory correction" as shown in the Shopify admin UI
- At least one of `staffMember` (non-null) or `app` (non-null) is present — excludes system-generated corrections with no actor
- `|sum(change.delta for change in changes)| >= MIN_ADJUSTMENT_QUANTITY` — excludes small incidental restocks (e.g. +1 or +2 units from refund "Restock item" checkbox). The absolute value of the summed delta is compared, so both increases and decreases must meet the threshold.

### Actor display priority

1. `staffMember.displayName` (human adjusted directly in Shopify on this store)
2. `app.title` (Knockify synced from another store, or adjustment made inside Knockify directly)
3. `"No Record"` (no qualifying adjustment found for this inventory item)

The most recent qualifying adjustment per inventory item is selected by comparing `node["createdAt"]` values across all qualifying edges and keeping the highest (most recent) datetime. From that winning record, the following values are extracted and stored together:
- `actor` — `staffMember.displayName` or `app.title`
- `date` — `createdAt` formatted as `YYYY-MM-DD`
- `delta` — `sum(change.delta for change in changes)` (net inventory change, signed)
- `qty_after` — `sum(change.quantityAfterChange for change in changes)` (total stock after adjustment)
- `qty_before` — `qty_after − delta` (total stock before adjustment)

### GraphQL throttle handling

`graphql_request()` handles throttling in two ways:
1. **Reactive:** If the response contains `errors` with `code == "THROTTLED"`, it sleeps with exponential backoff (`2^attempt` seconds) and retries up to `MAX_RETRIES` times.
2. **Proactive:** After each successful response, reads `response_json["extensions"]["cost"]["throttleStatus"]` for `currentlyAvailable` (points remaining) and `restoreRate` (points restored per second). If `currentlyAvailable < 200` and `restoreRate > 0`, sleeps `(200 - currentlyAvailable) / restoreRate` seconds before issuing the next request. If `restoreRate <= 0` (malformed response), falls back to a 2-second sleep.

---

## 8. Output Files

| File | Location | Notes |
|------|----------|-------|
| `report_YYYYMMDD.xlsx` | `OUTPUT_XLSX_DIR` | One sheet per store, named by `name` field. Date is UTC at script start. |
| `report_{NAME}_YYYYMMDD.csv` | `OUTPUT_CSV_DIR` | One file per store, same columns and order as `.xlsx`. Date is same UTC start time. |

Both directories are created automatically (`os.makedirs(..., exist_ok=True)`) if they don't exist.

**CSV encoding:** UTF-8 with BOM (`encoding="utf-8-sig"`) for direct Excel compatibility. Delimiter: comma. Column order matches the `.xlsx` column order exactly.

---

## 9. `.xlsx` Formatting

| Element | Behavior |
|---------|----------|
| Header row | Bold, frozen (row 1 stays visible when scrolling) |
| Column widths | Heuristic: `max(len(header), max(len(str(cell)) for all data cells)) × 1.2`, capped at 60 characters. The header text is included in the max-length scan to prevent truncated headers. |
| Never-sold rows | Light red background (`#FFD0D0`) — no sale found within `LAST_SOLD_LOOKBACK_DAYS` (column 6 value is `"Never Sold"`) |
| Shared Inventory cell | Yellow background (`#FFF2CC`) on column 14 cell only, when it contains `"Shared with: ..."` |
| Sort order | Highest `Days Since Last Sale` first per sheet |

---

## 10. Function Map

| Function | Phase | Responsibility |
|----------|-------|----------------|
| `safe_print()` | util | Thread-safe console output |
| `make_session()` | util | Requests session with connection pooling |
| `api_request()` | util | HTTP request with retry/backoff: respects `Retry-After` header on 429, exponential backoff on 5xx, retries on `ConnectionError`. Raises after `MAX_RETRIES` attempts. |
| `paginate()` | util | Follow Shopify REST pagination via `Link: rel="next"` headers |
| `graphql_request()` | util | GraphQL POST with THROTTLED error retry (exponential backoff, up to MAX_RETRIES) and proactive cost-bucket slow-down |
| `get_all_variants()` | Phase 1 | Fetch active product variants (`status=active`), includes `created_at` at product level |
| `get_order_data()` | Phase 1 | Scan order history, return `last_sold_map` + `recently_sold_ids` |
| `get_last_adjustment_map()` | Phase 1 | Batched GraphQL for last qualifying inventory adjustment per item. Returns `{ inventory_item_id → { date, days, actor, delta, qty_before, qty_after } }`. Qualifying criteria: `reason=="correction"`, actor present, `|sum(delta)| >= MIN_ADJUSTMENT_QUANTITY`. |
| `fetch_store_data()` | Phase 1 | Orchestrates the three fetches above for one store, returns structured dict |
| `build_shared_sku_map()` | Phase 2 | Cross-reference active SKUs across all stores |
| `build_report_rows()` | Phase 2 | Apply filters, compute all columns, return sorted row list |
| `write_xlsx()` | Phase 2 | Write single `.xlsx` workbook with formatting |
| `write_csv()` | Phase 2 | Write one CSV per store |
| `main()` | entry | Captures UTC start time, runs `ThreadPoolExecutor` for Phase 1, sequential Phase 2, prints error summary |

---

## 11. Removed from v2

| Removed | Reason |
|---------|--------|
| `"Over {THRESHOLD_DAYS}-Day Threshold?"` column | Redundant — every row already passed the threshold by definition |
| Monolithic `generate_report()` | Replaced by focused Phase 1/2 functions |
| `_sort_key` leaking into row dicts | Sorting handled before row dict construction |

---

## 12. Dependencies

| Package | Purpose | New? |
|---------|---------|------|
| `requests` | Shopify REST + GraphQL API calls | Existing |
| `openpyxl` | `.xlsx` file generation and formatting | **New** |

Install: `pip install requests openpyxl`

---

## 13. Known Limitations

| Limitation | Detail |
|------------|--------|
| **Inventory quantity is not location-aware** | `inventory_quantity` is fetched from the REST `/products.json` variants endpoint. This field reflects a sum across locations but behavior may vary by API version. A variant showing `inventory_quantity > 0` is included regardless of which specific location holds the stock. |
| **"Never Sold" includes items sold outside the lookback window** | A variant last sold before `LAST_SOLD_LOOKBACK_DAYS` ago will show `Last Sold Date: "Never Sold"` and fall back to `created_at` for the days calculation. No distinction is surfaced between "truly never sold" and "sold but too long ago to appear in the scan window." Increasing `LAST_SOLD_LOOKBACK_DAYS` extends API scan time proportionally. |
| **`created_at` is product-level, not variant-level** | Individual variant creation dates are not available via the Shopify REST API. The product's `created_at` is used as a proxy for the variant's age in the "never sold" fallback. |
| **Shared inventory detection degrades on partial store failures** | If a store fails Phase 1 entirely, its SKUs are absent from the shared-inventory cross-reference. Rows on other stores that would have shown "Shared with: [failed store]" will instead show "—". No warning is emitted for this specific case. |
| **`inventoryAdjustmentGroups` is an undocumented GraphQL field** | This field is not part of Shopify's public schema. It is confirmed working in v2 against API 2025-01 but may change in future API versions or be unavailable on certain store plan tiers. Null results are handled gracefully ("No Record"). |
| **Adjustment history capped at 50 records per item** | The GraphQL query uses `last: 50` to fetch the 50 most recent adjustment groups per inventory item. If a variant has more than 50 historical adjustments and its most recent *qualifying* record (reason=correction, actor present, |delta| >= MIN_ADJUSTMENT_QUANTITY) falls beyond that window, the script will silently return an older qualifying record — or "No Record" if none of the 50 visible records qualify. This is considered acceptable given that qualifying adjustments are typically large (>=5 units) and recent. |
