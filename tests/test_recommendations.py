"""Tests for recommendation engine and daily digest."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.common.db import (
    get_connection,
    init_db,
    save_action,
    save_daily_metrics,
    save_daily_metrics_batch,
    save_market_snapshot,
    upsert_inventory_item,
)
from src.common.models import (
    ActionLog,
    ActionType,
    DailyMetrics,
    FunnelAnalysis,
    FunnelStage,
    InventoryItem,
    ItemStatus,
    MarketListing,
    Marketplace,
    MarketSnapshot,
)
from src.performance_engine.recommendations import recommend_for_item, recommend_all
from src.performance_engine.digest import build_digest, format_digest_text


@pytest.fixture
def db():
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


def _make_item(db, item_id="item-001", name="MacBook Pro 13 2009",
               price=169.0, days_ago=10, status=ItemStatus.ACTIVE):
    item = InventoryItem(
        id=item_id, marketplace=Marketplace.WALLAPOP,
        marketplace_id=f"wp-{item_id}", name=name,
        listing_price=price,
        posted_date=datetime.now() - timedelta(days=days_ago),
        status=status,
    )
    upsert_inventory_item(db, item)
    return item


def _add_metrics(db, item_id, days=7, base_views=50, base_favs=5, base_msgs=1):
    metrics = [
        DailyMetrics(
            item_id=item_id, date=datetime(2025, 6, d),
            views=base_views + d * 3, favourites=base_favs + d, messages=base_msgs,
        )
        for d in range(1, days + 1)
    ]
    save_daily_metrics_batch(db, metrics)


def _add_market_snapshot(db, query="macbook", median_price=150.0):
    # Must include listings because save_market_snapshot calculates stats from them
    import uuid
    listings = [
        MarketListing(
            marketplace_id=str(uuid.uuid4()), marketplace=Marketplace.WALLAPOP,
            title=f"Listing {i}", price=median_price + (i - 3) * 10,
            scraped_at=datetime.now(tz=timezone.utc),
        )
        for i in range(5)
    ]
    snapshot = MarketSnapshot(
        query=query, marketplace=Marketplace.WALLAPOP,
        scraped_at=datetime.now(tz=timezone.utc),
        total_results=len(listings), listings=listings,
    )
    save_market_snapshot(db, snapshot)


# --- Recommendation Rules Tests ---

class TestRecommendForItem:
    def test_visibility_bottleneck_suggests_renew(self, db):
        item = _make_item(db)
        # Very low views → visibility bottleneck
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=10, total_favourites=2, total_messages=1,
            fav_rate=0.2, message_rate=0.5,
            bottleneck=FunnelStage.VISIBILITY,
            bottleneck_reason="Low visibility",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.RENEW in actions

    def test_attractiveness_bottleneck_with_high_price_suggests_price_drop(self, db):
        item = _make_item(db, price=200.0)
        _add_market_snapshot(db, query="macbook", median_price=150.0)

        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=5, total_messages=1,
            fav_rate=0.025, message_rate=0.2,
            bottleneck=FunnelStage.ATTRACTIVENESS,
            bottleneck_reason="Low fav rate",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        price_recs = [r for r in recs if r.action == ActionType.PRICE_CHANGE]
        assert len(price_recs) >= 1
        assert price_recs[0].suggested_value is not None
        assert "€" in price_recs[0].suggested_value

    def test_attractiveness_bottleneck_competitive_price_suggests_photos(self, db):
        item = _make_item(db, price=140.0)
        _add_market_snapshot(db, query="macbook", median_price=150.0)

        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=5, total_messages=1,
            fav_rate=0.025, message_rate=0.2,
            bottleneck=FunnelStage.ATTRACTIVENESS,
            bottleneck_reason="Low fav rate",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.PHOTO_CHANGE in actions

    def test_engagement_bottleneck_suggests_description(self, db):
        item = _make_item(db)
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=20, total_messages=1,
            fav_rate=0.10, message_rate=0.05,
            bottleneck=FunnelStage.ENGAGEMENT,
            bottleneck_reason="Low message rate",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.DESCRIPTION_CHANGE in actions

    def test_stale_listing_gets_renewal(self, db):
        item = _make_item(db, days_ago=20)
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=20, total_messages=5,
            fav_rate=0.10, message_rate=0.25,
            bottleneck=FunnelStage.CONVERSION,
            bottleneck_reason="Healthy funnel",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.RENEW in actions

    def test_very_stale_zero_messages_suggests_removal(self, db):
        item = _make_item(db, days_ago=60)
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=30,
            total_views=50, total_favourites=2, total_messages=0,
            bottleneck=FunnelStage.VISIBILITY,
            bottleneck_reason="Low visibility",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.REMOVE in actions

    def test_learns_from_failed_price_change(self, db):
        item = _make_item(db)
        # Log a price change with no_change outcome
        save_action(db, ActionLog(
            item_id=item.id, action=ActionType.PRICE_CHANGE,
            timestamp=datetime.now(tz=timezone.utc) - timedelta(days=3),
            outcome="no_change",
        ))

        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=5, total_messages=1,
            fav_rate=0.025, message_rate=0.2,
            bottleneck=FunnelStage.ATTRACTIVENESS,
            bottleneck_reason="Low fav rate",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        # Should suggest photos instead of another price change
        assert ActionType.PHOTO_CHANGE in actions
        assert ActionType.PRICE_CHANGE not in actions

    def test_recommendations_sorted_by_priority(self, db):
        item = _make_item(db, days_ago=20)
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=10, total_favourites=2, total_messages=0,
            bottleneck=FunnelStage.VISIBILITY,
            bottleneck_reason="Low visibility",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        priorities = [r.priority for r in recs]
        assert priorities == sorted(priorities, reverse=True)

    def test_declining_views_triggers_renewal(self, db):
        item = _make_item(db)
        funnel = FunnelAnalysis(
            item_id=item.id, period_days=7,
            total_views=200, total_favourites=20, total_messages=5,
            fav_rate=0.10, message_rate=0.25, views_delta=-15,
            bottleneck=FunnelStage.CONVERSION,
            bottleneck_reason="Healthy funnel",
        )
        recs = recommend_for_item(db, item, funnel=funnel)
        actions = [r.action for r in recs]
        assert ActionType.RENEW in actions


class TestRecommendAll:
    def test_returns_recs_for_active_items(self, db):
        _make_item(db, item_id="a", name="MacBook Pro", price=169, days_ago=5)
        _make_item(db, item_id="b", name="iPad Air", price=85, days_ago=20)
        _add_metrics(db, "a", base_views=2, base_favs=0, base_msgs=0)
        _add_metrics(db, "b", base_views=2, base_favs=0, base_msgs=0)

        result = recommend_all(db, days=7)
        assert "a" in result
        assert "b" in result


# --- Daily Digest Tests ---

class TestDigest:
    def test_build_digest(self, db):
        _make_item(db, item_id="a", name="MacBook Pro", price=169, days_ago=10)
        _make_item(db, item_id="b", name="iPad", price=35, days_ago=5)
        _add_metrics(db, "a", base_views=50, base_favs=5, base_msgs=1)
        _add_metrics(db, "b", base_views=20, base_favs=2, base_msgs=0)

        digest = build_digest(db, days=7)
        assert digest.active_count == 2
        assert digest.total_listed_value == 204.0
        assert len(digest.items) == 2

    def test_digest_sorted_by_priority(self, db):
        # Item with worse metrics should appear first
        _make_item(db, item_id="good", name="Good Item", price=50, days_ago=5)
        _make_item(db, item_id="bad", name="Bad Item", price=50, days_ago=30)
        _add_metrics(db, "good", base_views=50, base_favs=5, base_msgs=2)
        _add_metrics(db, "bad", base_views=2, base_favs=0, base_msgs=0)

        digest = build_digest(db, days=7)
        # Bad item should have higher priority recommendations → appear first
        if digest.items[0].recommendations and digest.items[1].recommendations:
            assert (digest.items[0].recommendations[0].priority
                    >= digest.items[1].recommendations[0].priority)

    def test_format_digest_text(self, db):
        _make_item(db, item_id="a", name="MacBook", price=169, days_ago=10)
        _add_metrics(db, "a", base_views=50, base_favs=5, base_msgs=1)

        digest = build_digest(db, days=7)
        text = format_digest_text(digest)

        assert "Daily Digest" in text
        assert "MacBook" in text
        assert "€169" in text
        assert "Active listings: 1" in text

    def test_empty_digest(self, db):
        digest = build_digest(db, days=7)
        assert digest.active_count == 0
        assert digest.items == []
        text = format_digest_text(digest)
        assert "Active listings: 0" in text
