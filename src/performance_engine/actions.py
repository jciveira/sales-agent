"""Action tracking with outcome measurement.

Tracks every action taken on a listing and measures its impact by comparing
metrics before the action with metrics at +24h and +72h windows.

Workflow:
  1. log_action()     — record action + snapshot metrics_before
  2. measure_outcomes() — called daily, fills in 24h/72h snapshots + outcome
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from ..common.db import (
    get_actions_pending_outcome,
    get_latest_metrics,
    save_action,
    update_action_outcome,
)
from ..common.models import ActionLog, ActionType, DailyMetrics

logger = logging.getLogger(__name__)


def log_action(
    conn: sqlite3.Connection,
    item_id: str,
    action: ActionType,
    details: dict | None = None,
    current_metrics: DailyMetrics | None = None,
) -> str:
    """Log an action taken on a listing, capturing current metrics as baseline.

    Args:
        conn: Database connection.
        item_id: Inventory item ID.
        action: Type of action taken.
        details: Action-specific data (e.g. old_price, new_price).
        current_metrics: Current metrics snapshot. If None, fetched from DB.

    Returns:
        The action log ID.
    """
    if current_metrics is None:
        current_metrics = get_latest_metrics(conn, item_id)

    metrics_before = {}
    if current_metrics:
        metrics_before = {
            "views": current_metrics.views,
            "favourites": current_metrics.favourites,
            "messages": current_metrics.messages,
            "date": current_metrics.date.strftime("%Y-%m-%d"),
        }

    entry = ActionLog(
        item_id=item_id,
        action=action,
        timestamp=datetime.now(tz=timezone.utc),
        details=details or {},
        metrics_before=metrics_before,
    )

    action_id = save_action(conn, entry)
    logger.info("Logged %s for item %s (id=%s)", action.value, item_id, action_id)
    return action_id


def measure_outcomes(conn: sqlite3.Connection) -> dict:
    """Check pending actions and fill in outcome measurements.

    Should be called daily (e.g. after the metric collector runs).
    For each pending action, checks if enough time has elapsed for the
    24h or 72h window, then snapshots current metrics and determines outcome.

    Returns:
        Summary dict with counts of updates made.
    """
    now = datetime.now(tz=timezone.utc)
    updated_24h = 0
    updated_72h = 0
    outcomes_set = 0

    # Process 24h window
    pending_24h = get_actions_pending_outcome(conn, window="24h")
    for action in pending_24h:
        elapsed = now - action.timestamp
        if elapsed < timedelta(hours=20):  # allow some slack
            continue

        current = get_latest_metrics(conn, action.item_id)
        if not current:
            continue

        metrics_after = {
            "views": current.views,
            "favourites": current.favourites,
            "messages": current.messages,
            "date": current.date.strftime("%Y-%m-%d"),
        }
        update_action_outcome(conn, action.id, metrics_after_24h=metrics_after)
        updated_24h += 1

    # Process 72h window
    pending_72h = get_actions_pending_outcome(conn, window="72h")
    for action in pending_72h:
        elapsed = now - action.timestamp
        if elapsed < timedelta(hours=68):  # allow some slack
            continue

        current = get_latest_metrics(conn, action.item_id)
        if not current:
            continue

        metrics_after = {
            "views": current.views,
            "favourites": current.favourites,
            "messages": current.messages,
            "date": current.date.strftime("%Y-%m-%d"),
        }

        # Determine outcome by comparing before vs after
        outcome = _determine_outcome(action.metrics_before, metrics_after)

        update_action_outcome(
            conn, action.id,
            metrics_after_72h=metrics_after,
            outcome=outcome,
        )
        updated_72h += 1
        outcomes_set += 1

    logger.info(
        "Outcome measurement: %d 24h updates, %d 72h updates, %d outcomes set",
        updated_24h, updated_72h, outcomes_set,
    )
    return {"updated_24h": updated_24h, "updated_72h": updated_72h, "outcomes_set": outcomes_set}


def _determine_outcome(before: dict, after: dict) -> str:
    """Compare before/after metrics to classify the action outcome."""
    if not before or not after:
        return "unknown"

    views_before = before.get("views", 0)
    views_after = after.get("views", 0)
    favs_before = before.get("favourites", 0)
    favs_after = after.get("favourites", 0)
    msgs_before = before.get("messages", 0)
    msgs_after = after.get("messages", 0)

    views_change = views_after - views_before
    favs_change = favs_after - favs_before
    msgs_change = msgs_after - msgs_before

    # Messages increased = strong positive signal
    if msgs_change > 0:
        return "improved_engagement"

    # Favourites increased meaningfully
    if favs_change > 0 and views_after > 0:
        return "improved_attractiveness"

    # Views increased but no downstream improvement
    if views_change > 0 and favs_change <= 0:
        return "improved_visibility_only"

    # Everything flat or declining
    if views_change <= 0 and favs_change <= 0 and msgs_change <= 0:
        return "no_change"

    return "mixed"
