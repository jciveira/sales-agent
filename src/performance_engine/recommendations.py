"""Recommendation engine — generates prioritized actions per item.

Two layers:
  1. Deterministic rules based on funnel bottleneck + market position + action history
  2. Claude API for narrative summary and nuanced suggestions (optional)
"""

import logging
import sqlite3
from datetime import datetime, timezone

from ..common.db import get_actions, get_inventory, get_snapshots
from ..common.models import (
    ActionType,
    FunnelAnalysis,
    FunnelStage,
    InventoryItem,
    ItemStatus,
    MarketSnapshot,
    Recommendation,
)
from .funnel import analyze_item

logger = logging.getLogger(__name__)

# Days listed before considering a price drop
STALE_THRESHOLD_DAYS = 14


def recommend_for_item(
    conn: sqlite3.Connection,
    item: InventoryItem,
    funnel: FunnelAnalysis | None = None,
    days: int = 30,
) -> list[Recommendation]:
    """Generate prioritized recommendations for a single item."""
    if funnel is None:
        funnel = analyze_item(conn, item.id, days=days)

    recs: list[Recommendation] = []

    # Market context
    market = _find_market_snapshot(conn, item)
    market_median = market.price_median if market else None

    # Recent action history
    recent_actions = get_actions(conn, item_id=item.id, limit=10)
    recent_types = {a.action for a in recent_actions}
    recent_outcomes = {a.action: a.outcome for a in recent_actions if a.outcome}

    now = datetime.now(tz=timezone.utc)
    posted = item.posted_date if item.posted_date.tzinfo else item.posted_date.replace(tzinfo=timezone.utc)
    days_listed = (now - posted).days

    # --- Rule-based recommendations by bottleneck stage ---

    if funnel.bottleneck == FunnelStage.VISIBILITY:
        # Low views → renew or relist
        if ActionType.RENEW not in recent_types:
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.RENEW,
                reasoning=funnel.bottleneck_reason,
                confidence=0.8,
                priority=90,
            ))
        else:
            # Already renewed recently — try description change for search
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.DESCRIPTION_CHANGE,
                reasoning=(
                    "Already renewed recently but visibility is still low. "
                    "Try adding more searchable keywords to the title/description."
                ),
                confidence=0.6,
                priority=70,
            ))

    elif funnel.bottleneck == FunnelStage.ATTRACTIVENESS:
        # Views OK but low favourites → price or photos
        if market_median and item.listing_price > market_median * 1.1:
            suggested = round(market_median * 0.95, 0)
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.PRICE_CHANGE,
                reasoning=(
                    f"{funnel.bottleneck_reason} "
                    f"Your price (€{item.listing_price:.0f}) is above market median "
                    f"(€{market_median:.0f}). Suggest dropping to €{suggested:.0f}."
                ),
                confidence=0.85,
                suggested_value=f"€{suggested:.0f}",
                priority=95,
            ))
        else:
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.PHOTO_CHANGE,
                reasoning=(
                    f"{funnel.bottleneck_reason} "
                    "Price is competitive — photos may need improvement. "
                    "Try better lighting, multiple angles, and showing condition details."
                ),
                confidence=0.65,
                priority=80,
            ))

    elif funnel.bottleneck == FunnelStage.ENGAGEMENT:
        # Good views+favs but few messages → description or shipping
        recs.append(Recommendation(
            item_id=item.id, item_name=item.name,
            action=ActionType.DESCRIPTION_CHANGE,
            reasoning=(
                f"{funnel.bottleneck_reason} "
                "Add specs, condition details, and shipping info to convert "
                "interested viewers into buyers."
            ),
            confidence=0.7,
            priority=75,
        ))

    elif funnel.bottleneck == FunnelStage.CONVERSION:
        # Funnel is healthy — no urgent action
        if days_listed > STALE_THRESHOLD_DAYS * 2 and market_median:
            suggested = round(market_median * 0.90, 0)
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.PRICE_CHANGE,
                reasoning=(
                    f"Listed for {days_listed} days with a healthy funnel but no sale. "
                    f"Small price drop to €{suggested:.0f} may close the deal."
                ),
                confidence=0.5,
                suggested_value=f"€{suggested:.0f}",
                priority=50,
            ))

    # --- Cross-cutting rules ---

    # Stale listing without recent renewal
    if days_listed > STALE_THRESHOLD_DAYS and ActionType.RENEW not in recent_types:
        if not any(r.action == ActionType.RENEW for r in recs):
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.RENEW,
                reasoning=f"Listed for {days_listed} days without renewal. Renewing boosts search visibility.",
                confidence=0.7,
                priority=60,
            ))

    # Declining metrics trend
    if funnel.views_delta < -10 and funnel.total_views > 0:
        if not any(r.action == ActionType.RENEW for r in recs):
            recs.append(Recommendation(
                item_id=item.id, item_name=item.name,
                action=ActionType.RENEW,
                reasoning=f"Views dropped by {abs(funnel.views_delta)} vs previous day. Renewal can recover visibility.",
                confidence=0.75,
                priority=85,
            ))

    # Learn from past outcomes: if price change previously had no_change, try photos instead
    if recent_outcomes.get(ActionType.PRICE_CHANGE) == "no_change":
        recs = [r for r in recs if r.action != ActionType.PRICE_CHANGE]
        recs.append(Recommendation(
            item_id=item.id, item_name=item.name,
            action=ActionType.PHOTO_CHANGE,
            reasoning="Previous price change had no effect. Try improving photos instead.",
            confidence=0.6,
            priority=75,
        ))

    # Consider removal for very stale items
    if days_listed > STALE_THRESHOLD_DAYS * 4 and funnel.total_messages == 0:
        recs.append(Recommendation(
            item_id=item.id, item_name=item.name,
            action=ActionType.REMOVE,
            reasoning=(
                f"Listed for {days_listed} days with zero messages. "
                "Consider removing and relisting with a fresh approach."
            ),
            confidence=0.4,
            priority=30,
        ))

    # Sort by priority descending
    recs.sort(key=lambda r: r.priority, reverse=True)
    return recs


def recommend_all(
    conn: sqlite3.Connection,
    days: int = 30,
) -> dict[str, list[Recommendation]]:
    """Generate recommendations for all active inventory items."""
    items = get_inventory(conn, status=ItemStatus.ACTIVE)
    result = {}
    for item in items:
        recs = recommend_for_item(conn, item, days=days)
        if recs:
            result[item.id] = recs
    return result


def _find_market_snapshot(
    conn: sqlite3.Connection,
    item: InventoryItem,
) -> MarketSnapshot | None:
    """Find the most relevant market snapshot for an item."""
    # Try first two words of item name as search query
    words = item.name.lower().split()
    for n in range(min(3, len(words)), 0, -1):
        query = " ".join(words[:n])
        snapshots = get_snapshots(conn, query=query, limit=1)
        if snapshots:
            return snapshots[0]

    # Fallback: search all snapshots for keyword match
    all_snapshots = get_snapshots(conn, limit=50)
    for s in all_snapshots:
        if any(w in s.query.lower() for w in words[:2]):
            return s
    return None
