"""Core data models for the sales agent."""

from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class ItemStatus(str, Enum):
    ACTIVE = "active"
    SOLD = "sold"
    REMOVED = "removed"
    RESERVED = "reserved"


class Marketplace(str, Enum):
    WALLAPOP = "wallapop"
    VINTED = "vinted"
    EBAY = "ebay"
    BUYCYCLE = "buycycle"


class InventoryItem(BaseModel):
    """An item in our inventory (things we're selling)."""
    id: str
    marketplace: Marketplace
    marketplace_id: str | None = None
    name: str
    description: str = ""
    category: str = ""
    listing_price: float
    purchase_price: float | None = None
    posted_date: datetime
    status: ItemStatus = ItemStatus.ACTIVE
    url: str | None = None


class MarketListing(BaseModel):
    """A listing found on a marketplace (competitor or potential buy)."""
    marketplace_id: str
    marketplace: Marketplace
    title: str
    description: str = ""
    price: float
    currency: str = "EUR"
    images: list[str] = []
    city: str = ""
    region: str = ""
    postal_code: str = ""
    latitude: float | None = None
    longitude: float | None = None
    category_id: int | None = None
    category_name: str = ""
    user_id: str = ""
    is_reserved: bool = False
    is_shippable: bool = False
    allows_shipping: bool = False
    web_slug: str = ""
    created_at: datetime | None = None
    modified_at: datetime | None = None
    scraped_at: datetime | None = None


class MarketSnapshot(BaseModel):
    """A point-in-time snapshot of market conditions for a search query."""
    id: str | None = None
    query: str
    marketplace: Marketplace
    scraped_at: datetime
    total_results: int
    listings: list[MarketListing] = []
    price_min: float | None = None
    price_max: float | None = None
    price_median: float | None = None
    price_avg: float | None = None


class DailyMetrics(BaseModel):
    """Daily performance metrics for an inventory item."""
    item_id: str
    date: datetime
    views: int = 0
    favourites: int = 0
    messages: int = 0


class FunnelStage(str, Enum):
    VISIBILITY = "visibility"       # Views — is the listing being seen?
    ATTRACTIVENESS = "attractiveness"  # Favs/Views — does it catch attention?
    ENGAGEMENT = "engagement"       # Messages/Favs — does it drive contact?
    CONVERSION = "conversion"       # Sale/Messages — does it close?


class FunnelAnalysis(BaseModel):
    """Conversion funnel analysis for an inventory item."""
    item_id: str
    period_days: int
    latest_date: datetime | None = None
    # Totals for the period
    total_views: int = 0
    total_favourites: int = 0
    total_messages: int = 0
    # Conversion rates
    fav_rate: float = 0.0       # favourites / views
    message_rate: float = 0.0   # messages / favourites
    contact_rate: float = 0.0   # messages / views (overall)
    # Day-over-day deltas (latest vs previous day)
    views_delta: int = 0
    favourites_delta: int = 0
    messages_delta: int = 0
    # Bottleneck
    bottleneck: FunnelStage = FunnelStage.VISIBILITY
    bottleneck_reason: str = ""


class ActionType(str, Enum):
    PRICE_CHANGE = "price_change"
    RENEW = "renew"
    PHOTO_CHANGE = "photo_change"
    RELIST = "relist"
    DESCRIPTION_CHANGE = "description_change"
    REMOVE = "remove"


class Recommendation(BaseModel):
    """A recommended action for an inventory item."""
    item_id: str
    item_name: str = ""
    action: "ActionType"
    reasoning: str
    confidence: float = 0.0  # 0.0–1.0
    suggested_value: str | None = None  # e.g. new price, or description tip
    priority: int = 0  # higher = more urgent


class DigestItem(BaseModel):
    """One item's section in the daily digest."""
    item: "InventoryItem"
    funnel: "FunnelAnalysis | None" = None
    recommendations: list["Recommendation"] = []
    recent_actions: list["ActionLog"] = []


class DailyDigest(BaseModel):
    """Daily performance digest across all inventory."""
    date: datetime
    active_count: int = 0
    total_listed_value: float = 0.0
    items: list["DigestItem"] = []
    summary: str = ""  # LLM-generated narrative summary


class ActionLog(BaseModel):
    """A logged action taken on an inventory item."""
    id: str | None = None
    item_id: str
    action: ActionType
    timestamp: datetime
    details: dict = {}
    metrics_before: dict = {}
    metrics_after_24h: dict | None = None
    metrics_after_72h: dict | None = None
    outcome: str | None = None
