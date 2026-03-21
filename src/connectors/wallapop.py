"""Wallapop marketplace connector using httpx + SSR extraction.

Data is extracted from the __NEXT_DATA__ SSR payload embedded in page HTML,
which provides all public data without authentication:
  - Profile page: user info + all published items (basic)
  - PDP page: full item detail including views, favourites, modifiedDate
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import httpx

from ..common.models import MarketListing, MarketSnapshot, Marketplace

logger = logging.getLogger(__name__)

CATEGORIES = {
    "electronics": 15000,
    "computers": 24200,
    "phones": 24201,
    "bikes": 24000,
}

BASE_URL = "https://es.wallapop.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
}

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


class _WallapopClient:
    """Async context manager that creates an httpx client warmed with cookies."""

    async def __aenter__(self) -> httpx.AsyncClient:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=30.0,
        )
        await self._client.get(BASE_URL)
        return self._client

    async def __aexit__(self, *exc):
        await self._client.aclose()


def _create_client() -> _WallapopClient:
    return _WallapopClient()


async def _fetch_ssr_data(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch a page and extract the __NEXT_DATA__ SSR payload."""
    resp = await client.get(url)
    resp.raise_for_status()
    match = _NEXT_DATA_RE.search(resp.text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Profile scraping
# ---------------------------------------------------------------------------

async def scrape_profile(profile_slug: str) -> dict:
    """Scrape a user's public profile page.

    Args:
        profile_slug: The slug from the profile URL (e.g. "juanc-18259777").

    Returns:
        Dict with keys: user (profile info) and items (list of published items).
    """
    async with _create_client() as client:
        url = f"{BASE_URL}/user/{profile_slug}"
        ssr = await _fetch_ssr_data(client, url)
        if not ssr:
            logger.warning("No SSR data found for profile %s", profile_slug)
            return {"user": {}, "items": []}

        props = ssr.get("props", {}).get("pageProps", {})
        user_data = props.get("user", {})
        published = props.get("publishedItems", {})
        items_raw = published.get("data", [])

        user = {
            "id": user_data.get("id", ""),
            "name": user_data.get("microName", ""),
            "slug": user_data.get("webSlug", profile_slug),
            "register_date": user_data.get("registerDate"),
            "avatar": user_data.get("avatarImage", ""),
            "city": user_data.get("location", {}).get("city", ""),
            "country_code": user_data.get("location", {}).get("countryCode", ""),
        }

        items = []
        for raw in items_raw:
            price_obj = raw.get("price", {})
            price = price_obj.get("amount", 0.0) if isinstance(price_obj, dict) else 0.0
            currency = price_obj.get("currency", "EUR") if isinstance(price_obj, dict) else "EUR"
            images = [
                img["urls"]["medium"]
                for img in raw.get("images", [])
                if "urls" in img and "medium" in img["urls"]
            ]
            items.append({
                "id": raw.get("id", ""),
                "title": raw.get("title", ""),
                "description": raw.get("description", ""),
                "slug": raw.get("slug", ""),
                "price": price,
                "currency": currency,
                "category_id": raw.get("categoryId"),
                "images": images,
                "is_reserved": raw.get("isReserved", False),
                "shipping": raw.get("shipping", {}),
            })

        return {"user": user, "items": items}


def scrape_profile_sync(profile_slug: str) -> dict:
    """Synchronous wrapper for scrape_profile()."""
    return asyncio.run(scrape_profile(profile_slug))


# ---------------------------------------------------------------------------
# Listing detail (PDP) scraping
# ---------------------------------------------------------------------------

async def scrape_listing(item_slug: str) -> dict | None:
    """Scrape a single listing's detail page (PDP).

    Returns all public data including views, favourites, modifiedDate,
    seller stats, and full item details.
    """
    async with _create_client() as client:
        return await _scrape_listing_with_client(client, item_slug)


async def _scrape_listing_with_client(client: httpx.AsyncClient, item_slug: str) -> dict | None:
    """Scrape a PDP using an existing HTTP client (for batch use)."""
    try:
        ssr = await _fetch_ssr_data(client, f"{BASE_URL}/item/{item_slug}")
        if not ssr:
            return None

        props = ssr.get("props", {}).get("pageProps", {})
        raw = props.get("item", {})
        if not raw:
            return None

        seller = props.get("itemSeller", {})

        # Parse title (can be string or dict with original/translated)
        title = raw.get("title", "")
        if isinstance(title, dict):
            title = title.get("original", "")

        # Parse description
        desc = raw.get("description", "")
        if isinstance(desc, dict):
            desc = desc.get("original", "")

        # Parse price
        price_obj = raw.get("price", {})
        cash = price_obj.get("cash", price_obj)
        price = cash.get("amount", 0.0) if isinstance(cash, dict) else 0.0
        currency = cash.get("currency", "EUR") if isinstance(cash, dict) else "EUR"

        # Parse images
        images = [
            img["urls"]["medium"]
            for img in raw.get("images", [])
            if "urls" in img and "medium" in img["urls"]
        ]

        # Parse modified date
        modified_ts = raw.get("modifiedDate")
        modified_date = None
        if modified_ts:
            modified_date = datetime.fromtimestamp(modified_ts / 1000, tz=timezone.utc).isoformat()

        # Parse location
        location = raw.get("location", {})

        # Parse flags
        flags = raw.get("flags", {})

        # Parse taxonomies
        taxonomies = raw.get("taxonomies", [])
        category_name = taxonomies[0]["name"] if taxonomies else ""
        category_id = int(taxonomies[0]["id"]) if taxonomies else None

        # Parse condition
        condition = raw.get("condition", {})

        # Seller stats
        seller_stats = seller.get("stats", {})
        seller_counters = seller_stats.get("counters", {})

        return {
            "id": raw.get("id", ""),
            "title": title,
            "description": desc,
            "slug": raw.get("slug", item_slug),
            "price": price,
            "currency": currency,
            "images": images,
            "views": raw.get("views", 0),
            "favourites": raw.get("favorites", raw.get("favourites", 0)),
            "modified_date": modified_date,
            "characteristics": raw.get("characteristics", ""),
            "brand": raw.get("brand"),
            "condition": condition.get("value") if isinstance(condition, dict) else None,
            "condition_text": condition.get("text") if isinstance(condition, dict) else None,
            "category_id": category_id,
            "category_name": category_name,
            "city": location.get("city", ""),
            "postal_code": location.get("postalCode", ""),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "is_reserved": flags.get("reserved", False),
            "is_sold": flags.get("sold", False),
            "is_expired": flags.get("expired", False),
            "is_shippable": raw.get("shipping", {}).get("isItemShippable", False),
            "allows_shipping": raw.get("shipping", {}).get("isShippingAllowedByUser", False),
            "user_id": raw.get("userId", ""),
            "seller": {
                "id": seller.get("id", ""),
                "name": seller.get("microName", ""),
                "slug": seller.get("webSlug", ""),
                "city": seller.get("location", {}).get("city", ""),
                "register_date": seller.get("registerDate"),
                "total_sales": seller_counters.get("sold", 0),
                "total_buys": seller_counters.get("buys", 0),
                "active_listings": seller_counters.get("publish", 0),
                "reviews": seller_stats.get("ratings", {}).get("reviews", 0),
            },
        }
    except Exception as e:
        logger.error("Failed to scrape listing %s: %s", item_slug, e)
        return None


def scrape_listing_sync(item_slug: str) -> dict | None:
    """Synchronous wrapper for scrape_listing()."""
    return asyncio.run(scrape_listing(item_slug))


# ---------------------------------------------------------------------------
# Combined: profile + PDP detail for all items
# ---------------------------------------------------------------------------

async def scrape_profile_with_details(profile_slug: str) -> dict:
    """Scrape profile and then each listing's PDP for full metrics.

    This is the main entry point for the daily collector. It:
    1. Loads the profile page to discover all published items
    2. Visits each item's PDP to get views, favourites, modifiedDate

    Returns:
        Dict with user info and items list (each with full PDP detail).
    """
    async with _create_client() as client:
        # Step 1: Profile page
        ssr = await _fetch_ssr_data(client, f"{BASE_URL}/user/{profile_slug}")

        if not ssr:
            logger.warning("No SSR data for profile %s", profile_slug)
            return {"user": {}, "items": []}

        props = ssr.get("props", {}).get("pageProps", {})
        user_data = props.get("user", {})
        items_raw = props.get("publishedItems", {}).get("data", [])

        user = {
            "id": user_data.get("id", ""),
            "name": user_data.get("microName", ""),
            "slug": user_data.get("webSlug", profile_slug),
            "register_date": user_data.get("registerDate"),
            "city": user_data.get("location", {}).get("city", ""),
        }

        # Step 2: PDP for each item
        items = []
        for raw in items_raw:
            slug = raw.get("slug", "")
            if not slug:
                continue
            logger.info("Scraping PDP: %s", raw.get("title", slug))
            detail = await _scrape_listing_with_client(client, slug)
            if detail:
                items.append(detail)
            else:
                # Fallback: use profile data without metrics
                price_obj = raw.get("price", {})
                items.append({
                    "id": raw.get("id", ""),
                    "title": raw.get("title", ""),
                    "slug": slug,
                    "price": price_obj.get("amount", 0.0) if isinstance(price_obj, dict) else 0.0,
                    "views": 0,
                    "favourites": 0,
                    "is_reserved": raw.get("isReserved", False),
                    "is_sold": False,
                })

        return {"user": user, "items": items}


def scrape_profile_with_details_sync(profile_slug: str) -> dict:
    """Synchronous wrapper for scrape_profile_with_details()."""
    return asyncio.run(scrape_profile_with_details(profile_slug))


# ---------------------------------------------------------------------------
# Market search
# ---------------------------------------------------------------------------

def _parse_listing(raw: dict) -> MarketListing:
    """Parse a raw Wallapop API item into a MarketListing."""
    price_obj = raw.get("price", {})
    price = price_obj.get("amount", 0.0) if isinstance(price_obj, dict) else float(price_obj or 0)
    currency = price_obj.get("currency", "EUR") if isinstance(price_obj, dict) else "EUR"

    location = raw.get("location", {})
    images = [
        img["urls"]["medium"]
        for img in raw.get("images", [])
        if "urls" in img and "medium" in img["urls"]
    ]
    taxonomy = raw.get("taxonomy", [])
    category_name = taxonomy[0]["name"] if taxonomy else ""
    category_id = taxonomy[0]["id"] if taxonomy else None

    created_at = None
    if raw.get("created_at"):
        created_at = datetime.fromtimestamp(raw["created_at"] / 1000, tz=timezone.utc)
    modified_at = None
    if raw.get("modified_at"):
        modified_at = datetime.fromtimestamp(raw["modified_at"] / 1000, tz=timezone.utc)

    return MarketListing(
        marketplace_id=raw.get("id", ""),
        marketplace=Marketplace.WALLAPOP,
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        price=price,
        currency=currency,
        images=images,
        city=location.get("city", ""),
        region=location.get("region", ""),
        postal_code=location.get("postal_code", ""),
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        category_id=category_id,
        category_name=category_name,
        user_id=raw.get("user_id", ""),
        is_reserved=raw.get("reserved", {}).get("flag", False),
        is_shippable=raw.get("shipping", {}).get("item_is_shippable", False),
        allows_shipping=raw.get("shipping", {}).get("user_allows_shipping", False),
        web_slug=raw.get("web_slug", ""),
        created_at=created_at,
        modified_at=modified_at,
        scraped_at=datetime.now(tz=timezone.utc),
    )


async def search(
    keywords: str,
    category_id: int | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    max_pages: int = 1,
) -> MarketSnapshot:
    """Search Wallapop via their public API and return a market snapshot."""
    params = {
        "keywords": keywords,
        "language": "es",
        "order_by": "newest",
        "items_count": 40,
    }
    if category_id:
        params["category_ids"] = str(category_id)
    if min_price is not None:
        params["min_sale_price"] = str(min_price)
    if max_price is not None:
        params["max_sale_price"] = str(max_price)

    all_items: list[dict] = []
    async with _create_client() as client:
        for page_num in range(max_pages):
            if page_num > 0:
                params["start"] = str(page_num * 40)
            resp = await client.get(
                "https://api.wallapop.com/api/v3/general/search/section",
                params=params,
            )
            if resp.status_code != 200:
                logger.warning("Search API returned %d", resp.status_code)
                break
            body = resp.json()
            items = body.get("data", {}).get("section", {}).get("items", [])
            all_items.extend(items)
            if not items:
                break

    listings = [_parse_listing(item) for item in all_items]

    return MarketSnapshot(
        query=keywords,
        marketplace=Marketplace.WALLAPOP,
        scraped_at=datetime.now(tz=timezone.utc),
        total_results=len(listings),
        listings=listings,
    )


def search_sync(
    keywords: str,
    category_id: int | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    max_pages: int = 1,
) -> MarketSnapshot:
    """Synchronous wrapper for search()."""
    return asyncio.run(search(keywords, category_id, min_price, max_price, max_pages))
