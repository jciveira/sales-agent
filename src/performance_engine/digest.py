"""Daily digest — assembles inventory health, funnel status, and recommendations.

Can optionally use Claude API to generate a narrative summary.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone

from ..common.db import get_actions, get_inventory
from ..common.models import (
    DailyDigest,
    DigestItem,
    ItemStatus,
)
from .funnel import analyze_item
from .recommendations import recommend_for_item

logger = logging.getLogger(__name__)


def build_digest(
    conn: sqlite3.Connection,
    days: int = 30,
    with_llm_summary: bool = False,
) -> DailyDigest:
    """Build the daily digest for all active inventory items."""
    items = get_inventory(conn, status=ItemStatus.ACTIVE)

    digest_items: list[DigestItem] = []
    for item in items:
        funnel = analyze_item(conn, item.id, days=days)
        recs = recommend_for_item(conn, item, funnel=funnel, days=days)
        recent_actions = get_actions(conn, item_id=item.id, limit=5)

        digest_items.append(DigestItem(
            item=item,
            funnel=funnel,
            recommendations=recs,
            recent_actions=recent_actions,
        ))

    # Sort by priority of top recommendation (most urgent first)
    digest_items.sort(
        key=lambda d: d.recommendations[0].priority if d.recommendations else 0,
        reverse=True,
    )

    total_value = sum(i.listing_price for i in items)

    digest = DailyDigest(
        date=datetime.now(tz=timezone.utc),
        active_count=len(items),
        total_listed_value=total_value,
        items=digest_items,
    )

    if with_llm_summary:
        digest.summary = _generate_llm_summary(digest)

    return digest


def format_digest_text(digest: DailyDigest) -> str:
    """Format the digest as plain text for CLI / notifications."""
    lines = [
        f"Daily Digest — {digest.date.strftime('%Y-%m-%d')}",
        f"Active listings: {digest.active_count} | Total value: €{digest.total_listed_value:.0f}",
        "",
    ]

    if digest.summary:
        lines.append(digest.summary)
        lines.append("")

    for di in digest.items:
        item = di.item
        days_listed = (datetime.now(tz=timezone.utc) - (item.posted_date if item.posted_date.tzinfo else item.posted_date.replace(tzinfo=timezone.utc))).days
        lines.append(f"--- {item.name} (€{item.listing_price:.0f}, {days_listed}d) ---")

        if di.funnel:
            f = di.funnel
            lines.append(
                f"  Funnel: {f.total_views} views | {f.total_favourites} favs "
                f"({f.fav_rate:.1%}) | {f.total_messages} msgs ({f.contact_rate:.1%})"
            )
            lines.append(f"  Bottleneck: {f.bottleneck.value} — {f.bottleneck_reason}")

        if di.recommendations:
            lines.append("  Recommendations:")
            for rec in di.recommendations:
                value_str = f" → {rec.suggested_value}" if rec.suggested_value else ""
                lines.append(
                    f"    [{rec.priority}] {rec.action.value}{value_str} "
                    f"(confidence: {rec.confidence:.0%})"
                )
                lines.append(f"         {rec.reasoning}")

        if di.recent_actions:
            lines.append("  Recent actions:")
            for a in di.recent_actions[:3]:
                outcome_str = f" → {a.outcome}" if a.outcome else " (pending)"
                lines.append(f"    {a.action.value} @ {a.timestamp.strftime('%m-%d')}{outcome_str}")

        lines.append("")

    return "\n".join(lines)


def _generate_llm_summary(digest: DailyDigest) -> str:
    """Use Claude API to generate a narrative summary of the digest."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping LLM summary")
        return ""

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — skipping LLM summary")
        return ""

    # Build context for the LLM
    items_context = []
    for di in digest.items:
        item_info = {
            "name": di.item.name,
            "price": di.item.listing_price,
            "days_listed": (datetime.now(tz=timezone.utc) - (di.item.posted_date if di.item.posted_date.tzinfo else di.item.posted_date.replace(tzinfo=timezone.utc))).days,
        }
        if di.funnel:
            item_info["funnel"] = {
                "views": di.funnel.total_views,
                "favourites": di.funnel.total_favourites,
                "messages": di.funnel.total_messages,
                "bottleneck": di.funnel.bottleneck.value,
            }
        if di.recommendations:
            item_info["top_recommendation"] = {
                "action": di.recommendations[0].action.value,
                "reasoning": di.recommendations[0].reasoning,
                "suggested_value": di.recommendations[0].suggested_value,
            }
        if di.recent_actions:
            item_info["recent_outcomes"] = [
                {"action": a.action.value, "outcome": a.outcome}
                for a in di.recent_actions[:3] if a.outcome
            ]
        items_context.append(item_info)

    prompt = (
        "You are a marketplace selling assistant. Generate a brief daily digest summary "
        "(3-5 sentences) for a seller with the following inventory:\n\n"
        f"Active listings: {digest.active_count}\n"
        f"Total listed value: €{digest.total_listed_value:.0f}\n\n"
        f"Items:\n{items_context}\n\n"
        "Focus on: what needs attention today, which items are performing well, "
        "and the single most impactful action the seller should take. "
        "Be concise, actionable, and specific."
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
