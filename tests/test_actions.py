"""Tests for action tracking and outcome measurement."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.common.db import (
    get_actions,
    get_actions_pending_outcome,
    get_connection,
    init_db,
    save_action,
    save_daily_metrics,
    update_action_outcome,
    upsert_inventory_item,
)
from src.common.models import (
    ActionLog,
    ActionType,
    DailyMetrics,
    InventoryItem,
    ItemStatus,
    Marketplace,
)
from src.performance_engine.actions import (
    _determine_outcome,
    log_action,
    measure_outcomes,
)


@pytest.fixture
def db():
    conn = get_connection(db_path=":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def item(db):
    item = InventoryItem(
        id="item-001", marketplace=Marketplace.WALLAPOP,
        marketplace_id="wp-001", name="MacBook Pro 13 2009",
        listing_price=169.0, posted_date=datetime(2025, 2, 25),
    )
    upsert_inventory_item(db, item)
    return item


@pytest.fixture
def item_with_metrics(db, item):
    save_daily_metrics(db, DailyMetrics(
        item_id="item-001", date=datetime(2025, 6, 10),
        views=100, favourites=8, messages=2,
    ))
    return item


# --- DB Layer Tests ---

class TestActionDB:
    def test_save_and_retrieve(self, db, item):
        action = ActionLog(
            item_id="item-001", action=ActionType.PRICE_CHANGE,
            timestamp=datetime(2025, 6, 10, 9, 0, tzinfo=timezone.utc),
            details={"old_price": 169, "new_price": 149},
            metrics_before={"views": 100, "favourites": 8, "messages": 2},
        )
        action_id = save_action(db, action)
        assert action_id

        actions = get_actions(db, item_id="item-001")
        assert len(actions) == 1
        assert actions[0].action == ActionType.PRICE_CHANGE
        assert actions[0].details["old_price"] == 169

    def test_get_actions_all(self, db, item):
        for i, atype in enumerate([ActionType.PRICE_CHANGE, ActionType.RENEW]):
            save_action(db, ActionLog(
                item_id="item-001", action=atype,
                timestamp=datetime(2025, 6, 10 + i, tzinfo=timezone.utc),
            ))
        actions = get_actions(db)
        assert len(actions) == 2

    def test_pending_outcome_24h(self, db, item):
        save_action(db, ActionLog(
            item_id="item-001", action=ActionType.RENEW,
            timestamp=datetime(2025, 6, 10, tzinfo=timezone.utc),
        ))
        pending = get_actions_pending_outcome(db, window="24h")
        assert len(pending) == 1

    def test_pending_outcome_resolved(self, db, item):
        action_id = save_action(db, ActionLog(
            item_id="item-001", action=ActionType.RENEW,
            timestamp=datetime(2025, 6, 10, tzinfo=timezone.utc),
        ))
        update_action_outcome(db, action_id,
                              metrics_after_24h={"views": 150})
        pending = get_actions_pending_outcome(db, window="24h")
        assert len(pending) == 0

    def test_update_outcome_fields(self, db, item):
        action_id = save_action(db, ActionLog(
            item_id="item-001", action=ActionType.PRICE_CHANGE,
            timestamp=datetime(2025, 6, 10, tzinfo=timezone.utc),
        ))
        update_action_outcome(db, action_id,
                              metrics_after_24h={"views": 120},
                              metrics_after_72h={"views": 180},
                              outcome="improved_engagement")
        actions = get_actions(db, item_id="item-001")
        assert actions[0].metrics_after_24h == {"views": 120}
        assert actions[0].metrics_after_72h == {"views": 180}
        assert actions[0].outcome == "improved_engagement"


# --- log_action Tests ---

class TestLogAction:
    def test_log_with_auto_metrics(self, db, item_with_metrics):
        action_id = log_action(
            db, item_id="item-001", action=ActionType.PRICE_CHANGE,
            details={"old_price": 169, "new_price": 149},
        )
        actions = get_actions(db, item_id="item-001")
        assert len(actions) == 1
        assert actions[0].metrics_before["views"] == 100
        assert actions[0].details["new_price"] == 149

    def test_log_with_explicit_metrics(self, db, item):
        metrics = DailyMetrics(
            item_id="item-001", date=datetime(2025, 6, 10),
            views=200, favourites=20, messages=5,
        )
        log_action(db, "item-001", ActionType.RENEW, current_metrics=metrics)

        actions = get_actions(db, item_id="item-001")
        assert actions[0].metrics_before["views"] == 200

    def test_log_without_metrics(self, db, item):
        # No metrics in DB — should still work with empty metrics_before
        action_id = log_action(db, "item-001", ActionType.RENEW)
        actions = get_actions(db, item_id="item-001")
        assert actions[0].metrics_before == {}


# --- Outcome Determination Tests ---

class TestDetermineOutcome:
    def test_improved_engagement(self):
        before = {"views": 100, "favourites": 8, "messages": 2}
        after = {"views": 120, "favourites": 12, "messages": 5}
        assert _determine_outcome(before, after) == "improved_engagement"

    def test_improved_attractiveness(self):
        before = {"views": 100, "favourites": 5, "messages": 2}
        after = {"views": 110, "favourites": 10, "messages": 2}
        assert _determine_outcome(before, after) == "improved_attractiveness"

    def test_improved_visibility_only(self):
        before = {"views": 50, "favourites": 5, "messages": 2}
        after = {"views": 100, "favourites": 5, "messages": 2}
        assert _determine_outcome(before, after) == "improved_visibility_only"

    def test_no_change(self):
        before = {"views": 100, "favourites": 8, "messages": 2}
        after = {"views": 95, "favourites": 7, "messages": 2}
        assert _determine_outcome(before, after) == "no_change"

    def test_unknown_empty(self):
        assert _determine_outcome({}, {"views": 10}) == "unknown"
        assert _determine_outcome({"views": 10}, {}) == "unknown"

    def test_mixed(self):
        # Views down, favs up, messages same — mixed signal
        before = {"views": 100, "favourites": 5, "messages": 2}
        after = {"views": 80, "favourites": 8, "messages": 2}
        assert _determine_outcome(before, after) == "improved_attractiveness"


# --- measure_outcomes Integration Tests ---

class TestMeasureOutcomes:
    def test_24h_measurement(self, db, item_with_metrics):
        # Log an action 25 hours ago
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=25)
        save_action(db, ActionLog(
            id="act-1", item_id="item-001", action=ActionType.PRICE_CHANGE,
            timestamp=old_time, metrics_before={"views": 80, "favourites": 5, "messages": 1},
        ))

        result = measure_outcomes(db)
        assert result["updated_24h"] == 1

        actions = get_actions(db, item_id="item-001")
        assert actions[0].metrics_after_24h is not None
        assert actions[0].metrics_after_24h["views"] == 100

    def test_72h_measurement_with_outcome(self, db, item_with_metrics):
        old_time = datetime.now(tz=timezone.utc) - timedelta(hours=73)
        save_action(db, ActionLog(
            id="act-2", item_id="item-001", action=ActionType.RENEW,
            timestamp=old_time,
            metrics_before={"views": 50, "favourites": 3, "messages": 1},
        ))

        result = measure_outcomes(db)
        assert result["updated_72h"] == 1
        assert result["outcomes_set"] == 1

        actions = get_actions(db, item_id="item-001")
        assert actions[0].outcome is not None

    def test_too_early_skipped(self, db, item_with_metrics):
        # Action just 5 hours ago — should not be measured yet
        recent_time = datetime.now(tz=timezone.utc) - timedelta(hours=5)
        save_action(db, ActionLog(
            id="act-3", item_id="item-001", action=ActionType.RENEW,
            timestamp=recent_time,
        ))

        result = measure_outcomes(db)
        assert result["updated_24h"] == 0
        assert result["updated_72h"] == 0

    def test_no_pending_actions(self, db):
        result = measure_outcomes(db)
        assert result == {"updated_24h": 0, "updated_72h": 0, "outcomes_set": 0}
