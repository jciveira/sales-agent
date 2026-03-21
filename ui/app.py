"""Sales Agent Dashboard — Streamlit UI."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.db import (
    get_actions,
    get_connection,
    get_daily_metrics,
    get_inventory,
    get_snapshot_listings,
    get_snapshots,
    init_db,
)
from src.common.models import (
    FunnelStage,
    ItemStatus,
    Marketplace,
)
from src.performance_engine.funnel import analyze_item, analyze_all
from src.performance_engine.recommendations import recommend_for_item
from src.performance_engine.digest import build_digest, format_digest_text

st.set_page_config(page_title="Sales Agent", page_icon="📊", layout="wide")

# --- Database ---
conn = get_connection()
init_db(conn)

# --- Sidebar ---
st.sidebar.title("Sales Agent")

# Profile configuration
profile_slug = st.sidebar.text_input(
    "Wallapop Profile Slug",
    value=st.session_state.get("profile_slug", ""),
    placeholder="e.g. juanc-18259777",
    help="From your Wallapop profile URL: wallapop.com/user/<slug>",
)
if profile_slug:
    st.session_state["profile_slug"] = profile_slug

# Collect metrics button
if st.sidebar.button("Collect Metrics", disabled=not profile_slug, type="primary"):
    with st.sidebar.status("Scraping Wallapop...", expanded=True) as status:
        try:
            from src.performance_engine.collector import collect_wallapop_metrics
            st.write("Discovering listings from profile...")
            metrics = collect_wallapop_metrics(profile_slug=profile_slug, conn=conn)
            status.update(label=f"Collected {len(metrics)} listings", state="complete")
            st.session_state["last_collection"] = datetime.now(tz=timezone.utc)
            st.rerun()
        except Exception as e:
            status.update(label=f"Error: {e}", state="error")

if "last_collection" in st.session_state:
    ts = st.session_state["last_collection"]
    st.sidebar.caption(f"Last collected: {ts.strftime('%Y-%m-%d %H:%M')} UTC")

st.sidebar.divider()
page = st.sidebar.radio("Navigate", ["Inventory", "Performance", "Daily Digest", "Market Intel", "Price Analysis"])


# --- Inventory Page ---
if page == "Inventory":
    st.title("📦 Inventory")

    items = get_inventory(conn)
    if not items:
        st.info("No items in inventory yet. Enter your Wallapop profile slug in the sidebar and click **Collect Metrics**.")
    else:
        # Summary cards
        active = [i for i in items if i.status == ItemStatus.ACTIVE]
        sold = [i for i in items if i.status == ItemStatus.SOLD]
        total_value = sum(i.listing_price for i in active)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Active Listings", len(active))
        col2.metric("Sold", len(sold))
        col3.metric("Total Listed Value", f"€{total_value:.0f}")
        col4.metric("Avg Price", f"€{total_value / len(active):.0f}" if active else "€0")

        # Inventory table
        st.subheader("Listings")
        for item in items:
            posted = item.posted_date if item.posted_date.tzinfo else item.posted_date.replace(tzinfo=timezone.utc)
            days_listed = (datetime.now(tz=timezone.utc) - posted).days
            latest = get_daily_metrics(conn, item.id, days=1)
            latest_m = latest[0] if latest else None

            status_icon = {"active": "🟢", "sold": "🔴", "reserved": "🟡"}.get(item.status.value, "⚪")

            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
                name_str = f"{status_icon} **{item.name}**"
                if item.url:
                    name_str += f"  [↗]({item.url})"
                c1.markdown(name_str)
                c2.metric("Price", f"€{item.listing_price:.0f}")
                c3.metric("Days Tracked", days_listed)
                c4.metric("Views", latest_m.views if latest_m else "—")
                c5.metric("Favs", latest_m.favourites if latest_m else "—")


# --- Performance Page ---
elif page == "Performance":
    st.title("Performance")

    items = get_inventory(conn, status=ItemStatus.ACTIVE)
    if not items:
        st.info("No active items in inventory.")
    else:
        # Overview: all items funnel summary
        funnels = analyze_all(conn, days=21)

        if funnels:
            st.subheader("Funnel Overview")

            # Bottleneck distribution chart
            bottleneck_counts = {}
            for f in funnels:
                label = f.bottleneck.value.capitalize()
                bottleneck_counts[label] = bottleneck_counts.get(label, 0) + 1

            fig_bn = go.Figure(data=[go.Pie(
                labels=list(bottleneck_counts.keys()),
                values=list(bottleneck_counts.values()),
                marker_colors=["#ff6b6b", "#ffa94d", "#ffd43b", "#69db7c"],
                hole=0.4,
            )])
            fig_bn.update_layout(title="Bottleneck Distribution", height=300)
            st.plotly_chart(fig_bn, use_container_width=True)

        # Per-item detail
        selected_item = st.selectbox(
            "Select item",
            items,
            format_func=lambda i: f"{i.name} (€{i.listing_price:.0f})",
        )

        if selected_item:
            funnel = analyze_item(conn, selected_item.id, days=21)
            metrics = get_daily_metrics(conn, selected_item.id, days=21)

            # Funnel metrics cards
            st.subheader(f"Funnel: {selected_item.name}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Views", funnel.total_views,
                       delta=f"{funnel.views_delta:+d}/day" if funnel.views_delta else None)
            c2.metric("Favourites", funnel.total_favourites,
                       delta=f"{funnel.favourites_delta:+d}/day" if funnel.favourites_delta else None)
            c3.metric("Messages", funnel.total_messages,
                       delta=f"{funnel.messages_delta:+d}/day" if funnel.messages_delta else None)
            c4.metric("Bottleneck", funnel.bottleneck.value.capitalize())

            # Conversion rates
            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("Fav Rate", f"{funnel.fav_rate:.1%}")
            rc2.metric("Message Rate", f"{funnel.message_rate:.1%}")
            rc3.metric("Contact Rate", f"{funnel.contact_rate:.1%}")

            st.info(funnel.bottleneck_reason)

            # Funnel bar chart
            fig_funnel = go.Figure(data=[go.Funnel(
                y=["Views", "Favourites", "Messages"],
                x=[funnel.total_views, funnel.total_favourites, funnel.total_messages],
                textinfo="value+percent initial",
                marker_color=["#339af0", "#ffa94d", "#51cf66"],
            )])
            fig_funnel.update_layout(title="Conversion Funnel", height=300)
            st.plotly_chart(fig_funnel, use_container_width=True)

            # Metrics trend over time
            if metrics:
                metrics_sorted = sorted(metrics, key=lambda m: m.date)
                dates = [m.date.strftime("%m-%d") for m in metrics_sorted]

                fig_trend = go.Figure()
                fig_trend.add_trace(go.Scatter(
                    x=dates, y=[m.views for m in metrics_sorted],
                    name="Views", line=dict(color="#339af0"),
                ))
                fig_trend.add_trace(go.Scatter(
                    x=dates, y=[m.favourites for m in metrics_sorted],
                    name="Favourites", line=dict(color="#ffa94d"),
                ))
                fig_trend.add_trace(go.Scatter(
                    x=dates, y=[m.messages for m in metrics_sorted],
                    name="Messages", line=dict(color="#51cf66"),
                ))
                fig_trend.update_layout(
                    title="Metrics Trend", xaxis_title="Date", yaxis_title="Count",
                    height=350,
                )
                st.plotly_chart(fig_trend, use_container_width=True)

            # Recommendations for this item
            recs = recommend_for_item(conn, selected_item, funnel=funnel, days=21)
            if recs:
                st.subheader("Recommendations")
                for rec in recs:
                    icon = {
                        "price_change": "💰", "renew": "🔄", "photo_change": "📸",
                        "description_change": "📝", "relist": "🔁", "remove": "🗑️",
                    }.get(rec.action.value, "📌")
                    value_str = f" **{rec.suggested_value}**" if rec.suggested_value else ""
                    with st.container(border=True):
                        st.markdown(
                            f"{icon} **{rec.action.value.replace('_', ' ').title()}**{value_str} "
                            f"— confidence: {rec.confidence:.0%}"
                        )
                        st.caption(rec.reasoning)

            # Recent actions
            actions = get_actions(conn, item_id=selected_item.id, limit=5)
            if actions:
                st.subheader("Recent Actions")
                for a in actions:
                    outcome_badge = ""
                    if a.outcome:
                        color = {"improved_engagement": "green", "improved_attractiveness": "blue",
                                 "no_change": "orange", "improved_visibility_only": "gray"}.get(a.outcome, "gray")
                        outcome_badge = f" :{'green' if 'improve' in (a.outcome or '') else 'orange'}[{a.outcome}]"
                    st.markdown(
                        f"- **{a.action.value.replace('_', ' ').title()}** "
                        f"@ {a.timestamp.strftime('%Y-%m-%d')}{outcome_badge}"
                    )


# --- Daily Digest Page ---
elif page == "Daily Digest":
    st.title("Daily Digest")

    digest = build_digest(conn, days=21)

    # Summary cards
    c1, c2, c3 = st.columns(3)
    c1.metric("Active Listings", digest.active_count)
    c2.metric("Total Listed Value", f"€{digest.total_listed_value:.0f}")
    items_needing_action = sum(1 for di in digest.items if di.recommendations)
    c3.metric("Items Needing Action", items_needing_action)

    if not digest.items:
        st.info("No active items to analyze.")
    else:
        # Priority action list
        st.subheader("Priority Actions")
        for di in digest.items:
            if not di.recommendations:
                continue
            top_rec = di.recommendations[0]
            posted = di.item.posted_date if di.item.posted_date.tzinfo else di.item.posted_date.replace(tzinfo=timezone.utc)
            days_listed = (datetime.now(tz=timezone.utc) - posted).days
            icon = {
                "price_change": "💰", "renew": "🔄", "photo_change": "📸",
                "description_change": "📝", "relist": "🔁", "remove": "🗑️",
            }.get(top_rec.action.value, "📌")

            with st.container(border=True):
                h1, h2, h3 = st.columns([3, 1, 1])
                h1.markdown(f"**{di.item.name}** — €{di.item.listing_price:.0f}")
                h2.markdown(f"📅 {days_listed}d listed")
                if di.funnel:
                    h3.markdown(f"🔍 {di.funnel.bottleneck.value.capitalize()}")

                # Top recommendation
                value_str = f" **{top_rec.suggested_value}**" if top_rec.suggested_value else ""
                st.markdown(
                    f"{icon} **{top_rec.action.value.replace('_', ' ').title()}**{value_str} "
                    f"(confidence: {top_rec.confidence:.0%})"
                )
                st.caption(top_rec.reasoning)

                # Quick funnel stats
                if di.funnel:
                    f = di.funnel
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric("Views", f.total_views)
                    mc2.metric("Fav Rate", f"{f.fav_rate:.1%}")
                    mc3.metric("Msg Rate", f"{f.message_rate:.1%}")
                    mc4.metric("Contact Rate", f"{f.contact_rate:.1%}")

        # Plain text digest (collapsible)
        with st.expander("View as text"):
            st.code(format_digest_text(digest), language=None)


# --- Market Intel Page ---
elif page == "Market Intel":
    st.title("🔍 Market Intel")

    snapshots = get_snapshots(conn, limit=50)
    if not snapshots:
        st.info("No market data yet. Run a search first.")
    else:
        # Group snapshots by query
        queries = sorted(set(s.query for s in snapshots))
        selected_query = st.selectbox("Select search query", queries)

        query_snapshots = [s for s in snapshots if s.query == selected_query]
        latest = query_snapshots[0]

        # Summary
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Listings", latest.total_results)
        col2.metric("Min Price", f"€{latest.price_min:.0f}" if latest.price_min else "—")
        col3.metric("Median Price", f"€{latest.price_median:.0f}" if latest.price_median else "—")
        col4.metric("Max Price", f"€{latest.price_max:.0f}" if latest.price_max else "—")

        # Listings table
        listings = get_snapshot_listings(conn, latest.id)

        if listings:
            st.subheader(f"Listings ({len(listings)})")

            # Price distribution chart
            prices = [l.price for l in listings if l.price > 0]
            if prices:
                fig = px.histogram(
                    x=prices,
                    nbins=20,
                    labels={"x": "Price (€)", "y": "Count"},
                    title="Price Distribution",
                )

                # Add line for our price if we have a matching inventory item
                inventory = get_inventory(conn)
                for item in inventory:
                    if any(word.lower() in selected_query.lower() for word in item.name.split()[:2]):
                        fig.add_vline(
                            x=item.listing_price,
                            line_dash="dash",
                            line_color="red",
                            annotation_text=f"Your price: €{item.listing_price:.0f}",
                        )
                        break

                st.plotly_chart(fig, use_container_width=True)

            # Listings detail
            for listing in listings:
                with st.container(border=True):
                    c1, c2, c3 = st.columns([4, 1, 1])
                    c1.markdown(f"**{listing.title}**")
                    c1.caption(listing.description[:120] + "..." if len(listing.description) > 120 else listing.description)
                    c2.metric("Price", f"€{listing.price:.0f}")
                    c3.write(f"📍 {listing.city}")
                    if listing.images:
                        cols = st.columns(min(len(listing.images), 4))
                        for i, img_url in enumerate(listing.images[:4]):
                            cols[i].image(img_url, width=150)


# --- Price Analysis Page ---
elif page == "Price Analysis":
    st.title("💰 Price Analysis")

    inventory = get_inventory(conn)
    snapshots = get_snapshots(conn, limit=50)

    if not inventory or not snapshots:
        st.info("Need both inventory and market data for price analysis.")
    else:
        st.subheader("Your Prices vs Market")

        rows = []
        for item in inventory:
            # Find best matching snapshot
            best_snapshot = None
            for s in snapshots:
                item_words = item.name.lower().split()
                if any(w in s.query.lower() for w in item_words[:2]):
                    best_snapshot = s
                    break

            if best_snapshot:
                rows.append({
                    "Item": item.name,
                    "Your Price": item.listing_price,
                    "Market Min": best_snapshot.price_min,
                    "Market Median": best_snapshot.price_median,
                    "Market Max": best_snapshot.price_max,
                    "Position": "Above" if item.listing_price > best_snapshot.price_median else "Below" if item.listing_price < best_snapshot.price_median else "At",
                })

        if rows:
            # Bar chart comparing prices
            fig = go.Figure()
            items_names = [r["Item"][:25] for r in rows]
            fig.add_trace(go.Bar(name="Market Min", x=items_names, y=[r["Market Min"] for r in rows], marker_color="lightblue"))
            fig.add_trace(go.Bar(name="Market Median", x=items_names, y=[r["Market Median"] for r in rows], marker_color="steelblue"))
            fig.add_trace(go.Bar(name="Your Price", x=items_names, y=[r["Your Price"] for r in rows], marker_color="red"))
            fig.add_trace(go.Bar(name="Market Max", x=items_names, y=[r["Market Max"] for r in rows], marker_color="lightgray"))
            fig.update_layout(barmode="group", title="Your Price vs Market", yaxis_title="Price (€)")
            st.plotly_chart(fig, use_container_width=True)

            # Detail cards
            for row in rows:
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.markdown(f"**{row['Item']}**")
                    c2.metric("Your Price", f"€{row['Your Price']:.0f}")
                    c3.metric("Market Median", f"€{row['Market Median']:.0f}")
                    pct = ((row["Your Price"] - row["Market Median"]) / row["Market Median"]) * 100
                    c4.metric("Position", row["Position"], delta=f"{pct:+.0f}%", delta_color="inverse")

conn.close()
