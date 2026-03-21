"""Tests for daily metric collection — DB layer and collector logic."""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.common.db import (
    get_connection,
    get_daily_metrics,
    get_inventory,
    get_latest_metrics,
    init_db,
    save_daily_metrics,
    save_daily_metrics_batch,
    upsert_inventory_item,
)
from src.common.models import DailyMetrics, InventoryItem, ItemStatus, Marketplace


@pytest.fixture
def db():
    """In-memory SQLite database for testing."""
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_item(db):
    """Insert a sample inventory item and return it."""
    item = InventoryItem(
        id="item-001",
        marketplace=Marketplace.WALLAPOP,
        marketplace_id="wp-001",
        name="MacBook Pro 13 2009",
        listing_price=169.0,
        posted_date=datetime(2025, 2, 25),
        status=ItemStatus.ACTIVE,
    )
    upsert_inventory_item(db, item)
    return item


# --- DB Layer Tests ---

class TestSaveDailyMetrics:
    def test_save_and_retrieve(self, db, sample_item):
        metrics = DailyMetrics(
            item_id=sample_item.id,
            date=datetime(2025, 6, 15),
            views=120, favourites=8, messages=3,
        )
        save_daily_metrics(db, metrics)

        result = get_daily_metrics(db, sample_item.id, days=30)
        assert len(result) == 1
        assert result[0].views == 120
        assert result[0].favourites == 8
        assert result[0].messages == 3

    def test_upsert_updates_existing(self, db, sample_item):
        date = datetime(2025, 6, 15)
        save_daily_metrics(db, DailyMetrics(
            item_id=sample_item.id, date=date, views=100, favourites=5, messages=2,
        ))
        save_daily_metrics(db, DailyMetrics(
            item_id=sample_item.id, date=date, views=150, favourites=10, messages=4,
        ))

        result = get_daily_metrics(db, sample_item.id)
        assert len(result) == 1
        assert result[0].views == 150

    def test_batch_save(self, db, sample_item):
        metrics_list = [
            DailyMetrics(item_id=sample_item.id, date=datetime(2025, 6, d), views=d * 10, favourites=d, messages=1)
            for d in range(1, 6)
        ]
        save_daily_metrics_batch(db, metrics_list)

        result = get_daily_metrics(db, sample_item.id, days=30)
        assert len(result) == 5

    def test_get_latest_metrics(self, db, sample_item):
        save_daily_metrics(db, DailyMetrics(
            item_id=sample_item.id, date=datetime(2025, 6, 1), views=50, favourites=2, messages=1,
        ))
        save_daily_metrics(db, DailyMetrics(
            item_id=sample_item.id, date=datetime(2025, 6, 5), views=200, favourites=15, messages=5,
        ))

        latest = get_latest_metrics(db, sample_item.id)
        assert latest is not None
        assert latest.views == 200

    def test_get_latest_metrics_empty(self, db):
        result = get_latest_metrics(db, "nonexistent")
        assert result is None

    def test_days_limit(self, db, sample_item):
        for d in range(1, 15):
            save_daily_metrics(db, DailyMetrics(
                item_id=sample_item.id, date=datetime(2025, 6, d), views=d, favourites=0, messages=0,
            ))
        result = get_daily_metrics(db, sample_item.id, days=5)
        assert len(result) == 5
        # Should be most recent first
        assert result[0].views == 14


# --- Collector Tests ---

FAKE_SCRAPE_RESPONSE = {
    "user": {"id": "user-123", "name": "Test User", "slug": "test-user"},
    "items": [
        {
            "id": "wp-001",
            "title": "MacBook Pro 13 2009",
            "description": "Good condition",
            "slug": "macbook-pro-13-2009-123",
            "price": 169.0,
            "views": 120,
            "favourites": 8,
            "is_reserved": False,
            "is_sold": False,
        },
        {
            "id": "wp-new",
            "title": "iPad Air 2",
            "description": "Like new",
            "slug": "ipad-air-2-456",
            "price": 85.0,
            "views": 45,
            "favourites": 2,
            "is_reserved": False,
            "is_sold": False,
        },
    ],
}


class TestCollector:
    @patch("src.performance_engine.collector.scrape_profile_with_details_sync")
    def test_collect_matches_existing_inventory(self, mock_scrape, db, sample_item):
        mock_scrape.return_value = {
            **FAKE_SCRAPE_RESPONSE,
            "items": [FAKE_SCRAPE_RESPONSE["items"][0]],
        }

        from src.performance_engine.collector import collect_wallapop_metrics
        metrics = collect_wallapop_metrics(profile_slug="test-user", conn=db)

        assert len(metrics) == 1
        assert metrics[0].item_id == "item-001"
        assert metrics[0].views == 120

        # Verify stored in DB
        stored = get_daily_metrics(db, "item-001")
        assert len(stored) == 1

    @patch("src.performance_engine.collector.scrape_profile_with_details_sync")
    def test_collect_auto_adds_new_listings(self, mock_scrape, db, sample_item):
        mock_scrape.return_value = FAKE_SCRAPE_RESPONSE

        from src.performance_engine.collector import collect_wallapop_metrics
        metrics = collect_wallapop_metrics(profile_slug="test-user", conn=db)

        assert len(metrics) == 2

        # New item should be in inventory now
        inventory = get_inventory(db)
        ids = [i.marketplace_id for i in inventory]
        assert "wp-new" in ids

    @patch("src.performance_engine.collector.scrape_profile_with_details_sync")
    def test_collect_marks_sold_items(self, mock_scrape, db, sample_item):
        sold_item = {**FAKE_SCRAPE_RESPONSE["items"][0], "is_sold": True}
        mock_scrape.return_value = {**FAKE_SCRAPE_RESPONSE, "items": [sold_item]}

        from src.performance_engine.collector import collect_wallapop_metrics
        collect_wallapop_metrics(profile_slug="test-user", conn=db)

        inventory = get_inventory(db)
        item = [i for i in inventory if i.marketplace_id == "wp-001"][0]
        assert item.status == ItemStatus.SOLD

    @patch("src.performance_engine.collector.scrape_profile_with_details_sync")
    def test_collect_syncs_price_changes(self, mock_scrape, db, sample_item):
        updated_item = {**FAKE_SCRAPE_RESPONSE["items"][0], "price": 149.0}
        mock_scrape.return_value = {**FAKE_SCRAPE_RESPONSE, "items": [updated_item]}

        from src.performance_engine.collector import collect_wallapop_metrics
        collect_wallapop_metrics(profile_slug="test-user", conn=db)

        inventory = get_inventory(db)
        item = [i for i in inventory if i.marketplace_id == "wp-001"][0]
        assert item.listing_price == 149.0

    @patch("src.performance_engine.collector.scrape_profile_with_details_sync")
    def test_collect_empty_response(self, mock_scrape, db):
        mock_scrape.return_value = {"user": {}, "items": []}

        from src.performance_engine.collector import collect_wallapop_metrics
        metrics = collect_wallapop_metrics(profile_slug="test-user", conn=db)

        assert metrics == []
