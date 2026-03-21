"""Daily metric collector — scrapes profile + PDP metrics and stores them.

New approach: user provides their public profile slug. The collector:
1. Scrapes the profile page to discover all published items
2. Scrapes each item's PDP to get views, favourites, modifiedDate
3. Auto-syncs inventory (adds new items, marks sold/reserved)
4. Saves daily metrics

No authentication needed — all data is public.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from ..common.db import (
    get_connection,
    get_inventory,
    init_db,
    save_daily_metrics_batch,
    upsert_inventory_item,
)
from ..common.models import DailyMetrics, InventoryItem, ItemStatus, Marketplace
from ..connectors.wallapop import scrape_profile_with_details_sync

logger = logging.getLogger(__name__)


def collect_wallapop_metrics(
    profile_slug: str,
    conn: sqlite3.Connection | None = None,
) -> list[DailyMetrics]:
    """Scrape metrics for all listings from a Wallapop profile.

    Args:
        profile_slug: Wallapop profile slug (e.g. "juanc-18259777").
        conn: Optional DB connection (caller manages lifecycle).

    Returns:
        List of DailyMetrics saved.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    init_db(conn)

    try:
        # 1. Scrape profile + PDP details
        logger.info("Scraping profile: %s", profile_slug)
        data = scrape_profile_with_details_sync(profile_slug)
        items = data.get("items", [])
        logger.info("Found %d listings on Wallapop", len(items))

        if not items:
            logger.warning("No listings found for profile %s", profile_slug)
            return []

        # 2. Load current inventory to match by marketplace_id
        inventory = get_inventory(conn)
        inv_by_mp_id: dict[str, InventoryItem] = {
            item.marketplace_id: item
            for item in inventory
            if item.marketplace == Marketplace.WALLAPOP and item.marketplace_id
        }

        today = datetime.now(tz=timezone.utc)
        metrics_list: list[DailyMetrics] = []

        for listing in items:
            mp_id = listing["id"]
            if not mp_id:
                continue

            inv_item = inv_by_mp_id.get(mp_id)

            if not inv_item:
                # New listing — auto-add to inventory
                logger.info("New listing discovered: %s", listing.get("title", ""))
                slug = listing.get("slug", "")
                inv_item = InventoryItem(
                    id=mp_id,
                    marketplace=Marketplace.WALLAPOP,
                    marketplace_id=mp_id,
                    name=listing.get("title", ""),
                    description=listing.get("description", ""),
                    listing_price=listing.get("price", 0),
                    posted_date=today,
                    status=ItemStatus.ACTIVE,
                    url=f"https://es.wallapop.com/item/{slug}" if slug else None,
                )
                upsert_inventory_item(conn, inv_item)
                inv_by_mp_id[mp_id] = inv_item

            # 3. Sync status + price
            is_sold = listing.get("is_sold", False)
            is_reserved = listing.get("is_reserved", False)
            new_price = listing.get("price", inv_item.listing_price)

            updated = False
            if is_sold and inv_item.status != ItemStatus.SOLD:
                inv_item.status = ItemStatus.SOLD
                updated = True
                logger.info("Marked as sold: %s", inv_item.name)
            elif is_reserved and inv_item.status != ItemStatus.RESERVED:
                inv_item.status = ItemStatus.RESERVED
                updated = True
                logger.info("Marked as reserved: %s", inv_item.name)
            if new_price != inv_item.listing_price:
                inv_item.listing_price = new_price
                updated = True
                logger.info("Price updated: %s → €%.0f", inv_item.name, new_price)
            if updated:
                upsert_inventory_item(conn, inv_item)

            # 4. Build daily metrics
            metrics = DailyMetrics(
                item_id=inv_item.id,
                date=today,
                views=listing.get("views", 0),
                favourites=listing.get("favourites", 0),
                messages=0,  # not publicly available
            )
            metrics_list.append(metrics)

        # 5. Save all metrics
        save_daily_metrics_batch(conn, metrics_list)
        logger.info("Saved daily metrics for %d items", len(metrics_list))

        return metrics_list
    finally:
        if own_conn:
            conn.close()
