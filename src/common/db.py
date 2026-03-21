"""SQLite storage for the sales agent."""

import json
import sqlite3
import statistics
import uuid
from datetime import datetime
from pathlib import Path

from .models import (
    ActionLog,
    DailyMetrics,
    InventoryItem,
    ItemStatus,
    Marketplace,
    MarketListing,
    MarketSnapshot,
)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sales_agent.db"


def get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (
            id TEXT PRIMARY KEY,
            marketplace TEXT NOT NULL,
            marketplace_id TEXT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            category TEXT DEFAULT '',
            listing_price REAL NOT NULL,
            purchase_price REAL,
            posted_date TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            url TEXT
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scraped_at TEXT NOT NULL,
            total_results INTEGER,
            price_min REAL,
            price_max REAL,
            price_median REAL,
            price_avg REAL
        );

        CREATE TABLE IF NOT EXISTS market_listings (
            marketplace_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            price REAL NOT NULL,
            currency TEXT DEFAULT 'EUR',
            images TEXT DEFAULT '[]',
            city TEXT DEFAULT '',
            region TEXT DEFAULT '',
            postal_code TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            category_id INTEGER,
            category_name TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            is_reserved INTEGER DEFAULT 0,
            is_shippable INTEGER DEFAULT 0,
            allows_shipping INTEGER DEFAULT 0,
            web_slug TEXT DEFAULT '',
            created_at TEXT,
            modified_at TEXT,
            scraped_at TEXT,
            PRIMARY KEY (marketplace_id, snapshot_id),
            FOREIGN KEY (snapshot_id) REFERENCES market_snapshots(id)
        );

        CREATE TABLE IF NOT EXISTS daily_metrics (
            item_id TEXT NOT NULL,
            date TEXT NOT NULL,
            views INTEGER DEFAULT 0,
            favourites INTEGER DEFAULT 0,
            messages INTEGER DEFAULT 0,
            PRIMARY KEY (item_id, date),
            FOREIGN KEY (item_id) REFERENCES inventory(id)
        );

        CREATE TABLE IF NOT EXISTS action_log (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            metrics_before TEXT DEFAULT '{}',
            metrics_after_24h TEXT,
            metrics_after_72h TEXT,
            outcome TEXT,
            FOREIGN KEY (item_id) REFERENCES inventory(id)
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_query ON market_snapshots(query, marketplace);
        CREATE INDEX IF NOT EXISTS idx_listings_snapshot ON market_listings(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_metrics_item ON daily_metrics(item_id, date);
        CREATE INDEX IF NOT EXISTS idx_actions_item ON action_log(item_id, timestamp);
    """)
    conn.commit()


# --- Inventory ---

def upsert_inventory_item(conn: sqlite3.Connection, item: InventoryItem) -> None:
    conn.execute(
        """INSERT INTO inventory (id, marketplace, marketplace_id, name, description,
           category, listing_price, purchase_price, posted_date, status, url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             marketplace_id=excluded.marketplace_id, name=excluded.name,
             description=excluded.description, category=excluded.category,
             listing_price=excluded.listing_price, purchase_price=excluded.purchase_price,
             status=excluded.status, url=excluded.url""",
        (item.id, item.marketplace.value, item.marketplace_id, item.name,
         item.description, item.category, item.listing_price, item.purchase_price,
         item.posted_date.isoformat(), item.status.value, item.url),
    )
    conn.commit()


def get_inventory(conn: sqlite3.Connection, status: ItemStatus | None = None) -> list[InventoryItem]:
    if status:
        rows = conn.execute("SELECT * FROM inventory WHERE status = ?", (status.value,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM inventory").fetchall()
    return [
        InventoryItem(
            id=r["id"], marketplace=Marketplace(r["marketplace"]),
            marketplace_id=r["marketplace_id"], name=r["name"],
            description=r["description"], category=r["category"],
            listing_price=r["listing_price"], purchase_price=r["purchase_price"],
            posted_date=datetime.fromisoformat(r["posted_date"]),
            status=ItemStatus(r["status"]), url=r["url"],
        )
        for r in rows
    ]


# --- Market Snapshots ---

def save_market_snapshot(conn: sqlite3.Connection, snapshot: MarketSnapshot) -> str:
    snapshot_id = snapshot.id or str(uuid.uuid4())
    prices = [l.price for l in snapshot.listings if l.price > 0]

    conn.execute(
        """INSERT INTO market_snapshots (id, query, marketplace, scraped_at,
           total_results, price_min, price_max, price_median, price_avg)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, snapshot.query, snapshot.marketplace.value,
         snapshot.scraped_at.isoformat(), snapshot.total_results,
         min(prices) if prices else None,
         max(prices) if prices else None,
         statistics.median(prices) if prices else None,
         statistics.mean(prices) if prices else None),
    )

    for listing in snapshot.listings:
        conn.execute(
            """INSERT OR REPLACE INTO market_listings
               (marketplace_id, snapshot_id, marketplace, title, description, price,
                currency, images, city, region, postal_code, latitude, longitude,
                category_id, category_name, user_id, is_reserved, is_shippable,
                allows_shipping, web_slug, created_at, modified_at, scraped_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (listing.marketplace_id, snapshot_id, listing.marketplace.value,
             listing.title, listing.description, listing.price, listing.currency,
             json.dumps(listing.images), listing.city, listing.region,
             listing.postal_code, listing.latitude, listing.longitude,
             listing.category_id, listing.category_name, listing.user_id,
             int(listing.is_reserved), int(listing.is_shippable),
             int(listing.allows_shipping), listing.web_slug,
             listing.created_at.isoformat() if listing.created_at else None,
             listing.modified_at.isoformat() if listing.modified_at else None,
             listing.scraped_at.isoformat() if listing.scraped_at else None),
        )

    conn.commit()
    return snapshot_id


def get_snapshots(conn: sqlite3.Connection, query: str | None = None,
                  marketplace: Marketplace | None = None,
                  limit: int = 10) -> list[MarketSnapshot]:
    sql = "SELECT * FROM market_snapshots WHERE 1=1"
    params: list = []
    if query:
        sql += " AND query = ?"
        params.append(query)
    if marketplace:
        sql += " AND marketplace = ?"
        params.append(marketplace.value)
    sql += " ORDER BY scraped_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [
        MarketSnapshot(
            id=r["id"], query=r["query"],
            marketplace=Marketplace(r["marketplace"]),
            scraped_at=datetime.fromisoformat(r["scraped_at"]),
            total_results=r["total_results"],
            price_min=r["price_min"], price_max=r["price_max"],
            price_median=r["price_median"], price_avg=r["price_avg"],
        )
        for r in rows
    ]


# --- Daily Metrics ---

def save_daily_metrics(conn: sqlite3.Connection, metrics: DailyMetrics) -> None:
    conn.execute(
        """INSERT INTO daily_metrics (item_id, date, views, favourites, messages)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(item_id, date) DO UPDATE SET
             views=excluded.views, favourites=excluded.favourites,
             messages=excluded.messages""",
        (metrics.item_id, metrics.date.strftime("%Y-%m-%d"),
         metrics.views, metrics.favourites, metrics.messages),
    )
    conn.commit()


def save_daily_metrics_batch(conn: sqlite3.Connection, metrics_list: list[DailyMetrics]) -> None:
    conn.executemany(
        """INSERT INTO daily_metrics (item_id, date, views, favourites, messages)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(item_id, date) DO UPDATE SET
             views=excluded.views, favourites=excluded.favourites,
             messages=excluded.messages""",
        [(m.item_id, m.date.strftime("%Y-%m-%d"), m.views, m.favourites, m.messages)
         for m in metrics_list],
    )
    conn.commit()


def get_daily_metrics(
    conn: sqlite3.Connection,
    item_id: str,
    days: int = 30,
) -> list[DailyMetrics]:
    rows = conn.execute(
        """SELECT * FROM daily_metrics
           WHERE item_id = ?
           ORDER BY date DESC LIMIT ?""",
        (item_id, days),
    ).fetchall()
    return [
        DailyMetrics(
            item_id=r["item_id"],
            date=datetime.strptime(r["date"], "%Y-%m-%d"),
            views=r["views"], favourites=r["favourites"], messages=r["messages"],
        )
        for r in rows
    ]


def get_latest_metrics(conn: sqlite3.Connection, item_id: str) -> DailyMetrics | None:
    row = conn.execute(
        "SELECT * FROM daily_metrics WHERE item_id = ? ORDER BY date DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    return DailyMetrics(
        item_id=row["item_id"],
        date=datetime.strptime(row["date"], "%Y-%m-%d"),
        views=row["views"], favourites=row["favourites"], messages=row["messages"],
    )


# --- Action Log ---

def save_action(conn: sqlite3.Connection, action: ActionLog) -> str:
    action_id = action.id or str(uuid.uuid4())
    conn.execute(
        """INSERT INTO action_log (id, item_id, action, timestamp, details,
           metrics_before, metrics_after_24h, metrics_after_72h, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             metrics_after_24h=excluded.metrics_after_24h,
             metrics_after_72h=excluded.metrics_after_72h,
             outcome=excluded.outcome""",
        (action_id, action.item_id, action.action.value,
         action.timestamp.isoformat(),
         json.dumps(action.details), json.dumps(action.metrics_before),
         json.dumps(action.metrics_after_24h) if action.metrics_after_24h else None,
         json.dumps(action.metrics_after_72h) if action.metrics_after_72h else None,
         action.outcome),
    )
    conn.commit()
    return action_id


def get_actions(
    conn: sqlite3.Connection,
    item_id: str | None = None,
    limit: int = 50,
) -> list[ActionLog]:
    sql = "SELECT * FROM action_log WHERE 1=1"
    params: list = []
    if item_id:
        sql += " AND item_id = ?"
        params.append(item_id)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [_row_to_action(r) for r in rows]


def get_actions_pending_outcome(
    conn: sqlite3.Connection,
    window: str = "24h",
) -> list[ActionLog]:
    """Get actions that need outcome measurement at the given window."""
    col = "metrics_after_24h" if window == "24h" else "metrics_after_72h"
    rows = conn.execute(
        f"SELECT * FROM action_log WHERE {col} IS NULL ORDER BY timestamp ASC",
        (),
    ).fetchall()
    return [_row_to_action(r) for r in rows]


def update_action_outcome(
    conn: sqlite3.Connection,
    action_id: str,
    metrics_after_24h: dict | None = None,
    metrics_after_72h: dict | None = None,
    outcome: str | None = None,
) -> None:
    sets = []
    params: list = []
    if metrics_after_24h is not None:
        sets.append("metrics_after_24h = ?")
        params.append(json.dumps(metrics_after_24h))
    if metrics_after_72h is not None:
        sets.append("metrics_after_72h = ?")
        params.append(json.dumps(metrics_after_72h))
    if outcome is not None:
        sets.append("outcome = ?")
        params.append(outcome)
    if not sets:
        return
    params.append(action_id)
    conn.execute(f"UPDATE action_log SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()


def _row_to_action(r) -> ActionLog:
    return ActionLog(
        id=r["id"], item_id=r["item_id"],
        action=r["action"], timestamp=datetime.fromisoformat(r["timestamp"]),
        details=json.loads(r["details"]) if r["details"] else {},
        metrics_before=json.loads(r["metrics_before"]) if r["metrics_before"] else {},
        metrics_after_24h=json.loads(r["metrics_after_24h"]) if r["metrics_after_24h"] else None,
        metrics_after_72h=json.loads(r["metrics_after_72h"]) if r["metrics_after_72h"] else None,
        outcome=r["outcome"],
    )


def get_snapshot_listings(conn: sqlite3.Connection, snapshot_id: str) -> list[MarketListing]:
    rows = conn.execute(
        "SELECT * FROM market_listings WHERE snapshot_id = ? ORDER BY price ASC",
        (snapshot_id,),
    ).fetchall()
    return [
        MarketListing(
            marketplace_id=r["marketplace_id"],
            marketplace=Marketplace(r["marketplace"]),
            title=r["title"], description=r["description"],
            price=r["price"], currency=r["currency"],
            images=json.loads(r["images"]),
            city=r["city"], region=r["region"],
            postal_code=r["postal_code"],
            latitude=r["latitude"], longitude=r["longitude"],
            category_id=r["category_id"], category_name=r["category_name"],
            user_id=r["user_id"],
            is_reserved=bool(r["is_reserved"]),
            is_shippable=bool(r["is_shippable"]),
            allows_shipping=bool(r["allows_shipping"]),
            web_slug=r["web_slug"],
            created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            modified_at=datetime.fromisoformat(r["modified_at"]) if r["modified_at"] else None,
            scraped_at=datetime.fromisoformat(r["scraped_at"]) if r["scraped_at"] else None,
        )
        for r in rows
    ]
