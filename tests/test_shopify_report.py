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
