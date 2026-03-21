"""Tests for funnel analysis — pure computation and DB integration."""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.common.db import (
    get_connection,
    init_db,
    save_daily_metrics,
    save_daily_metrics_batch,
    upsert_inventory_item,
)
from src.common.models import (
    DailyMetrics,
    FunnelStage,
    InventoryItem,
    ItemStatus,
    Marketplace,
)
from src.performance_engine.funnel import (
    _compute_funnel,
    _identify_bottleneck,
    analyze_all,
    analyze_item,
)


@pytest.fixture
def db():
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def item_with_metrics(db):
    """Item with 7 days of metrics showing a healthy funnel."""
    item = InventoryItem(
        id="item-001", marketplace=Marketplace.WALLAPOP,
        marketplace_id="wp-001", name="MacBook Pro 13 2009",
        listing_price=169.0, posted_date=datetime(2025, 2, 25),
    )
    upsert_inventory_item(db, item)

    metrics = [
        DailyMetrics(item_id="item-001", date=datetime(2025, 6, d),
                     views=50 + d * 5, favourites=5 + d, messages=1 + (d % 3))
        for d in range(1, 8)
    ]
    save_daily_metrics_batch(db, metrics)
    return item


# --- Pure computation tests ---

class TestComputeFunnel:
    def test_empty_metrics(self):
        result = _compute_funnel("item-x", [], 30)
        assert result.bottleneck == FunnelStage.VISIBILITY
        assert result.total_views == 0

    def test_basic_rates(self):
        metrics = [
            DailyMetrics(item_id="x", date=datetime(2025, 6, 2), views=100, favourites=10, messages=3),
            DailyMetrics(item_id="x", date=datetime(2025, 6, 1), views=80, favourites=8, messages=2),
        ]
        result = _compute_funnel("x", metrics, 7)
        assert result.total_views == 180
        assert result.total_favourites == 18
        assert result.total_messages == 5
        assert result.fav_rate == round(18 / 180, 4)
        assert result.message_rate == round(5 / 18, 4)
        assert result.contact_rate == round(5 / 180, 4)

    def test_day_over_day_deltas(self):
        metrics = [
            DailyMetrics(item_id="x", date=datetime(2025, 6, 3), views=120, favourites=15, messages=5),
            DailyMetrics(item_id="x", date=datetime(2025, 6, 2), views=100, favourites=10, messages=3),
        ]
        result = _compute_funnel("x", metrics, 7)
        assert result.views_delta == 20
        assert result.favourites_delta == 5
        assert result.messages_delta == 2

    def test_single_day_no_delta(self):
        metrics = [
            DailyMetrics(item_id="x", date=datetime(2025, 6, 1), views=50, favourites=5, messages=1),
        ]
        result = _compute_funnel("x", metrics, 1)
        assert result.views_delta == 0

    def test_zero_views_no_division_error(self):
        metrics = [
            DailyMetrics(item_id="x", date=datetime(2025, 6, 1), views=0, favourites=0, messages=0),
        ]
        result = _compute_funnel("x", metrics, 1)
        assert result.fav_rate == 0.0
        assert result.message_rate == 0.0
        assert result.contact_rate == 0.0


class TestIdentifyBottleneck:
    def test_low_views(self):
        stage, reason = _identify_bottleneck(views=10, favs=5, msgs=2, fav_rate=0.5, msg_rate=0.4, num_days=7)
        assert stage == FunnelStage.VISIBILITY
        assert "visibility" in reason.lower()

    def test_low_fav_rate(self):
        stage, _ = _identify_bottleneck(views=200, favs=4, msgs=2, fav_rate=0.02, msg_rate=0.5, num_days=7)
        assert stage == FunnelStage.ATTRACTIVENESS

    def test_low_message_rate(self):
        stage, _ = _identify_bottleneck(views=200, favs=20, msgs=1, fav_rate=0.10, msg_rate=0.05, num_days=7)
        assert stage == FunnelStage.ENGAGEMENT

    def test_healthy_funnel(self):
        stage, reason = _identify_bottleneck(views=500, favs=50, msgs=15, fav_rate=0.10, msg_rate=0.30, num_days=7)
        assert stage == FunnelStage.CONVERSION
        assert "healthy" in reason.lower()


# --- DB integration tests ---

class TestAnalyzeItem:
    def test_analyze_with_data(self, db, item_with_metrics):
        result = analyze_item(db, "item-001", days=30)
        assert result.item_id == "item-001"
        assert result.total_views > 0
        assert result.latest_date is not None
        assert result.bottleneck in FunnelStage

    def test_analyze_no_data(self, db):
        upsert_inventory_item(db, InventoryItem(
            id="item-empty", marketplace=Marketplace.WALLAPOP,
            name="Empty Item", listing_price=50.0, posted_date=datetime(2025, 6, 1),
        ))
        result = analyze_item(db, "item-empty", days=30)
        assert result.total_views == 0
        assert result.bottleneck == FunnelStage.VISIBILITY

    def test_analyze_all(self, db, item_with_metrics):
        # Add a second active item with no metrics
        upsert_inventory_item(db, InventoryItem(
            id="item-002", marketplace=Marketplace.WALLAPOP,
            name="iPad", listing_price=35.0, posted_date=datetime(2025, 6, 1),
        ))
        results = analyze_all(db, days=30, active_only=True)
        assert len(results) == 2
        ids = {r.item_id for r in results}
        assert "item-001" in ids
        assert "item-002" in ids

    def test_analyze_all_excludes_sold(self, db, item_with_metrics):
        upsert_inventory_item(db, InventoryItem(
            id="item-sold", marketplace=Marketplace.WALLAPOP,
            name="Sold Thing", listing_price=99.0,
            posted_date=datetime(2025, 3, 1), status=ItemStatus.SOLD,
        ))
        results = analyze_all(db, days=30, active_only=True)
        ids = {r.item_id for r in results}
        assert "item-sold" not in ids
