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
