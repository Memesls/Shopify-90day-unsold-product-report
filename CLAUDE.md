# Project Memory — Shopify 90-Day Unsold Inventory Report

## What This Project Does

A Python script (`shopify_last_sold_report.py`) that queries multiple Shopify stores via the Admin API and generates a report of active product variants that:
- Have **not sold in over 90 days**
- Are **currently in stock** (inventory_quantity > 0)
- Have **active** product status (not draft or archived)

Output: one `.xlsx` file (all stores, one sheet each) + individual CSV files per store.

---

## Store Setup

- We manage **4+ Shopify stores**
- Each store is defined in the `STORES` list with three fields: `shop`, `token`, `name`
- The `name` field (e.g. `"CBSD"`, `"LOSAD"`) is used in sheet tabs, CSV filenames, and shared inventory labels

---

## Shared Inventory

- Some products/SKUs exist across multiple stores simultaneously with shared inventory
- Shared inventory is identified by **matching SKU** across stores
- Only **active** variants are cross-referenced (archived on one store ≠ shared)
- Blank SKUs (`""`) are excluded from shared detection
- In the report, the **Shared Inventory** column shows `"Shared with: CBSD, LOSAD"` when a SKU is active on other stores, or `"—"` if unique

---

## Inventory Adjustments

- We use a third-party tool called **Knockify** (appears as `"Knockify-2.2"` in adjustment history)
- Knockify syncs inventory changes across stores automatically
- When a human adjusts inventory directly in Shopify Store A: Store A shows the staff member's name, other stores show `"Knockify-2.2"`
- When an adjustment is made inside the Knockify tool directly: all stores show `"Knockify-2.2"`
- The report captures the **most recent qualifying manual correction** per variant
- Qualifying criteria: `reason == "correction"`, actor (staffMember or app) present, AND `|sum(delta)| >= MIN_ADJUSTMENT_QUANTITY` (default: 5 units)
- The `MIN_ADJUSTMENT_QUANTITY` filter excludes small incidental restocks from refunds/replacements (typically +1 or +2 units from the "Restock item" checkbox in Shopify)
- Staff member name takes priority over app name in the **Adjusted By** column
- The adjustment map stores per-item: `{ date, days, actor, delta, qty_before, qty_after }`

---

## Output Files

| File | Location |
|------|----------|
| `report_YYYYMMDD.xlsx` | `D:\Claude Projects\Knocking 90-day unsold inventory\` |
| `report_{NAME}_YYYYMMDD.csv` | `D:\Claude Projects\Knocking 90-day unsold inventory\CSV files\` |

---

## Report Columns (in order)

1. Product Title
2. Product Variant
3. SKU
4. Vendor
5. Available Inventory
6. Last Sold Date (`YYYY-MM-DD` or `"Never Sold"`)
7. Days Since Last Sale (falls back to days since `created_at` if never sold)
8. Last Inventory Adjustment (`YYYY-MM-DD` or `"No Record"`)
9. Days Since Last Adjustment (integer or `"No Record"`)
10. Adjusted By (staff name or app name, e.g. `"Knockify-2.2"`)
11. Previous Inventory (stock before the adjustment, integer or `"No Record"`)
12. Adjustment Quantity (net delta, plain signed integer or `"No Record"`)
13. New Inventory (stock after the adjustment, integer or `"No Record"`)
14. Shared Inventory (`"—"` or `"Shared with: ..."`)

---

## Architecture — Two-Phase Pipeline

### Phase 1 (parallel, one thread per store)
- Fetch active variants (`status=active`, includes `created_at`)
- Scan order history (last 365 days)
- Query inventory adjustment history via batched GraphQL

### Phase 2 (sequential)
- `build_shared_sku_map()` — cross-reference SKUs across all stores
- `build_report_rows()` — apply filters, compute columns
- `write_xlsx()` — single workbook with formatting
- `write_csv()` — one CSV per store

---

## Configuration Constants

| Constant | Default | Purpose |
|----------|---------|---------|
| `THRESHOLD_DAYS` | `90` | Unsold threshold for report inclusion |
| `LAST_SOLD_LOOKBACK_DAYS` | `365` | Order history scan window |
| `MIN_ADJUSTMENT_QUANTITY` | `5` | Min absolute delta to qualify an adjustment |
| `GRAPHQL_BATCH_SIZE` | `25` | Items per GraphQL nodes() request |
| `STORE_WORKERS` | `4` | Max parallel store threads |
| `MAX_RETRIES` | `6` | API retry limit |

---

## Key Design Decisions & Context

- **Never-sold fallback**: If a variant has no sales in the lookback window, `Days Since Last Sale` falls back to days since the product's `created_at` date (so it sorts above recently-threshold-crossed items)
- **Active-only filtering**: `status=active` is enforced at the Shopify API level (query param), not post-fetch
- **Qualifying adjustments**: `reason == "correction"` + staffMember OR app present (excludes system corrections)
- **xlsx formatting**: Bold frozen header, auto-width columns, red rows for never-sold, yellow cell for shared inventory
- **Sort order**: Highest `Days Since Last Sale` first per sheet/store

---

## Dependencies

```
pip install requests openpyxl
```

---

## Design Spec

Full design document: `docs/superpowers/specs/2026-03-23-shopify-90-day-unsold-report-design.md`
