"""Conversion funnel analysis for inventory items.

Funnel stages:
  Views → Favourites → Messages → Sale

Each transition maps to a listing issue:
  - Low views           → visibility problem (search ranking, renewal timing)
  - Low fav/view rate   → attractiveness problem (price, photos, title)
  - Low msg/fav rate    → engagement problem (description, shipping options)
  - Low sale/msg rate   → conversion problem (negotiation, response time)
"""

import sqlite3

from ..common.db import get_daily_metrics, get_inventory
from ..common.models import (
    DailyMetrics,
    FunnelAnalysis,
    FunnelStage,
    InventoryItem,
    ItemStatus,
)

# Thresholds for bottleneck detection (based on Wallapop marketplace norms)
# These are starting heuristics — will be tuned with real data.
MIN_DAILY_VIEWS = 5
MIN_FAV_RATE = 0.05      # 5% of viewers favourite
MIN_MESSAGE_RATE = 0.15   # 15% of favouriters message


def analyze_item(
    conn: sqlite3.Connection,
    item_id: str,
    days: int = 30,
) -> FunnelAnalysis:
    """Compute funnel analysis for a single inventory item."""
    metrics = get_daily_metrics(conn, item_id, days=days)
    return _compute_funnel(item_id, metrics, days)


def analyze_all(
    conn: sqlite3.Connection,
    days: int = 30,
    active_only: bool = True,
) -> list[FunnelAnalysis]:
    """Compute funnel analysis for all inventory items."""
    status = ItemStatus.ACTIVE if active_only else None
    items = get_inventory(conn, status=status)
    return [analyze_item(conn, item.id, days) for item in items]


def _compute_funnel(
    item_id: str,
    metrics: list[DailyMetrics],
    period_days: int,
) -> FunnelAnalysis:
    """Pure computation — no DB access, easy to test."""
    if not metrics:
        return FunnelAnalysis(
            item_id=item_id,
            period_days=period_days,
            bottleneck=FunnelStage.VISIBILITY,
            bottleneck_reason="No metrics data available",
        )

    total_views = sum(m.views for m in metrics)
    total_favs = sum(m.favourites for m in metrics)
    total_msgs = sum(m.messages for m in metrics)

    fav_rate = total_favs / total_views if total_views > 0 else 0.0
    message_rate = total_msgs / total_favs if total_favs > 0 else 0.0
    contact_rate = total_msgs / total_views if total_views > 0 else 0.0

    # Day-over-day deltas (metrics are sorted newest first)
    views_delta = 0
    favs_delta = 0
    msgs_delta = 0
    if len(metrics) >= 2:
        views_delta = metrics[0].views - metrics[1].views
        favs_delta = metrics[0].favourites - metrics[1].favourites
        msgs_delta = metrics[0].messages - metrics[1].messages

    # Identify bottleneck
    bottleneck, reason = _identify_bottleneck(
        total_views, total_favs, total_msgs,
        fav_rate, message_rate, len(metrics),
    )

    return FunnelAnalysis(
        item_id=item_id,
        period_days=period_days,
        latest_date=metrics[0].date if metrics else None,
        total_views=total_views,
        total_favourites=total_favs,
        total_messages=total_msgs,
        fav_rate=round(fav_rate, 4),
        message_rate=round(message_rate, 4),
        contact_rate=round(contact_rate, 4),
        views_delta=views_delta,
        favourites_delta=favs_delta,
        messages_delta=msgs_delta,
        bottleneck=bottleneck,
        bottleneck_reason=reason,
    )


def _identify_bottleneck(
    views: int,
    favs: int,
    msgs: int,
    fav_rate: float,
    msg_rate: float,
    num_days: int,
) -> tuple[FunnelStage, str]:
    """Determine which funnel stage is the weakest link."""
    avg_daily_views = views / num_days if num_days > 0 else 0

    if avg_daily_views < MIN_DAILY_VIEWS:
        return (
            FunnelStage.VISIBILITY,
            f"Low visibility: {avg_daily_views:.1f} views/day (need >{MIN_DAILY_VIEWS}). "
            "Consider renewing the listing or improving the title for search.",
        )

    if fav_rate < MIN_FAV_RATE:
        return (
            FunnelStage.ATTRACTIVENESS,
            f"Low attractiveness: {fav_rate:.1%} fav rate (need >{MIN_FAV_RATE:.0%}). "
            "Price may be too high, or photos/title need improvement.",
        )

    if msg_rate < MIN_MESSAGE_RATE:
        return (
            FunnelStage.ENGAGEMENT,
            f"Low engagement: {msg_rate:.1%} message rate from favouriters (need >{MIN_MESSAGE_RATE:.0%}). "
            "Description may lack detail, or shipping options could help.",
        )

    return (
        FunnelStage.CONVERSION,
        "Funnel looks healthy. Focus on response speed and negotiation to close sales.",
    )
