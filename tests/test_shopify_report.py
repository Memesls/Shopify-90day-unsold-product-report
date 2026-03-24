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
