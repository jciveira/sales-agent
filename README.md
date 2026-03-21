# Sales Agent

A personal AI-powered marketplace trading assistant that helps buy and sell across multiple platforms (Wallapop, Vinted, eBay, Buycycle). Maximizes sale revenue, minimizes time-to-sell, and finds the best deals when buying.

## Problem

Trading second-hand items online is time-consuming and under-optimized:

- **Selling**: Sellers rely on gut feeling for pricing, generic listing copy, and reactive negotiation. Marketplace dashboards show vanity metrics (views, favourites) but offer no actionable strategy. After two weeks of manual tracking with basic trend-based recommendations, **zero sales** — proving that view/favourite deltas alone don't drive conversions.
- **Buying**: Evaluating whether a listing is a good deal requires manual research — checking comparable prices, assessing seller trustworthiness from ad wording and profile, and devising a negotiation strategy. All of this is done ad-hoc with no structured support.

## What This Agent Does

Two operating modes, both informed by real market data:

### Sell Mode
1. **Listing Optimization** — Analyzes competing listings to generate the right price, title, description, and photo strategy for maximum conversion
2. **Performance Intelligence** — Automates metric collection, tracks the full conversion funnel (views → favs → messages → sale), and recommends actions based on what actually works
3. **Sell-side Negotiation** — Profiles buyers from their messages and marketplace profile, suggests response strategies, and helps close deals at the best price

### Buy Mode
4. **Deal Evaluation** — Analyzes a listing (URL, screenshots, or manual input) against market data to assess whether the price is fair, good, or overpriced
5. **Seller Profiling** — Evaluates seller trustworthiness from ad wording, description quality, photo authenticity, profile history, and response patterns
6. **Buy-side Negotiation** — Devises an offer strategy based on fair market value and seller profile (e.g., firm seller vs. desperate seller), suggests opening offers, counter-offers, and walk-away points
7. **Deal Tracking** — Monitors watched listings for price drops, re-listings, and seller behavior changes; sends proactive alerts

### Key Design Principle

Every recommendation is tracked for outcome. The agent learns which actions lead to sales and which don't, building an effectiveness feedback loop over time.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 USER INTERFACES                  │
│  WhatsApp / Telegram / Web Dashboard / CLI       │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│              ORCHESTRATION LAYER                 │
│         (Agent core — Claude API)                │
│  Routes intents, manages workflows, holds state  │
└──┬───────────┬───────────┬──────────┬───────────┘
   │           │           │          │
   ▼           ▼           ▼          ▼
┌──────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
│LISTING│ │PERFORMA-│ │NEGOTIA- │ │ MARKET   │
│OPTIMI-│ │NCE      │ │TION     │ │ INTEL    │
│ZER    │ │ENGINE   │ │ASSISTANT│ │ SERVICE  │
└──┬────┘ └──┬──────┘ └──┬──────┘ └──┬───────┘
   │         │           │           │
   ▼         ▼           ▼           ▼
┌─────────────────────────────────────────────────┐
│              DATA LAYER                          │
│  Inventory / Metrics History / Market Snapshots  │
│  Buyer Profiles / Action Log / Outcome Tracking  │
│              (SQLite → DynamoDB)                 │
└──────────────────────┬──────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────┐
│           MARKETPLACE CONNECTORS                 │
│  Wallapop / Vinted / eBay / Buycycle             │
│  (Scraping / APIs / Screenshot analysis)         │
└─────────────────────────────────────────────────┘
```

## Engines

| Engine | Sell Mode | Buy Mode | Approach |
|---|---|---|---|
| **Market Intel** | Competitor pricing, demand signals, sold items | Fair value assessment, deal scoring | Deterministic collection + LLM analysis |
| **Listing Optimizer** | Generate title/description/price/photo strategy | — | LLM with market data as context |
| **Performance Engine** | Track conversion funnel, trigger actions | Track watched items, price drop alerts | Deterministic math, LLM for strategy |
| **Negotiation** | Profile buyers, suggest responses, close deals | Profile sellers, devise offer strategy, counter-offers | LLM with counterparty data as context |

## Data Model

### Inventory
- Item ID, name, category, purchase price, listing price, posting date, marketplace, status (active/sold/removed)

### Daily Metrics (per item, per marketplace)
- Date, views, favourites, messages received, position in search results

### Market Snapshots (per category/search)
- Date, number of competing listings, price range (min/median/max), recently sold prices, average days-to-sell

### Action Log
- Date, item, action taken (renew/price change/photo change/relist), metrics before, metrics after, outcome

### Counterparty Profiles (buyers when selling, sellers when buying)
- Profile ID, role (buyer/seller), message history, profile signals, negotiation stage, proposed prices

### Watchlist (buy mode)
- Listing URL, item description, target price, fair market value, seller profile, status (watching/negotiating/bought/passed)
- Price history (tracking seller's price changes over time)

## Conversion Funnel

The key insight: track where potential buyers drop off.

```
Views → Favourites → Messages → Negotiation → Sale
  │         │            │           │
  │         │            │           └─ Negotiation quality (Agent helps here)
  │         │            └─ Listing appeal (photos, description, price signal)
  │         └─ Interest level (price competitiveness)
  └─ Visibility (search ranking, category, time of day)
```

Each stage has different levers. The agent identifies which stage is the bottleneck and recommends accordingly.

## Tech Stack

- **Language**: Python 3.12+
- **LLM**: Claude API (via Bedrock, or direct Anthropic API for personal use)
- **Storage**: SQLite
- **Marketplace access**: httpx (HTTP client) + SSR extraction (`__NEXT_DATA__`)
- **UI**: Streamlit
- **Interface**: Streamlit dashboard + CLI → WhatsApp/Telegram
- **Scheduling**: cron

## Deployment Stages

Build for local-first. Refactor only when there's a reason to.

| Stage | Host | Stack | Access | When |
|---|---|---|---|---|
| **MVP** | MacBook | SQLite, Streamlit, manual + cron | localhost | Now |
| **Cheap host** | Raspberry Pi 4 | Same stack, cron for scrapers, always-on | LAN or Tailscale/Cloudflare Tunnel | Once MVP works end-to-end |
| **Production** | Cloud (TBD, personal account) | Containerized, managed DB, proper auth | Public | If/when it outgrows Pi4 |

### Pi4 Considerations
- ARM64 — same arch as M1 Mac, no cross-compilation needed
- Python 3.12+, SQLite, cron all native
- Streamlit accessible on LAN; expose externally via Tailscale (free) or Cloudflare Tunnel (free)
- WhatsApp/Telegram bots run fine on Pi4
- Personal project — no cloud dependencies until production stage

## Project Structure

```
sales-agent/
├── src/
│   ├── market_intel/       # Marketplace scraping and competitive analysis
│   ├── listing_optimizer/  # Listing creation and optimization
│   ├── performance_engine/ # Metrics tracking, funnel analysis, recommendations
│   ├── negotiation/        # Buyer profiling and response strategies
│   ├── connectors/         # Marketplace-specific adapters (Wallapop, Vinted, etc.)
│   └── common/             # Shared models, DB, config, Claude API client
├── ui/                     # Streamlit dashboard
├── tests/
│   └── fixtures/           # Saved HTML/JSON marketplace snapshots for testing
├── docs/                   # Architecture decisions, API research, marketplace notes
├── data/                   # SQLite DB, market snapshots (gitignored)
└── README.md
```

## Roadmap

### Phase 1 — Market Intel + Listing Optimizer (Wallapop) ✅
- [x] Wallapop connector (scrape search results, listing details, sold items)
- [x] Market snapshot storage and analysis
- [ ] Listing text/price generator with competitive context
- [ ] CLI interface for creating optimized listings
- [x] Streamlit dashboard: inventory table, market snapshots, price distribution

### Phase 2 — Performance Engine ✅
- [x] Automated daily metric collection (profile + PDP scraping via httpx)
- [x] SQLite storage with full history
- [x] Conversion funnel analysis
- [x] Action tracking with outcome measurement
- [x] Recommendation engine that learns from results
- [x] Daily digest with prioritized actions
- [x] Streamlit Cloud deployment (no Playwright — pure httpx + SSR extraction)

### Phase 3 — Buy Mode (Deal Evaluation + Negotiation)
- [ ] Listing analysis from URL/screenshot/manual input
- [ ] Fair value calculator (market data comparison)
- [ ] Seller profiling (ad quality, wording, profile history, response patterns)
- [ ] Buy-side negotiation strategy (opening offer, counters, walk-away price)
- [ ] Watchlist with price drop tracking and alerts

### Phase 4 — Sell-side Negotiation
- [ ] Buyer profile extraction from messages + marketplace profile
- [ ] Response suggestion engine
- [ ] Deal scoring (probability of sale at given price)
- [ ] Counter-offer strategy based on market data

### Phase 5 — Multi-channel + Multi-marketplace
- [ ] WhatsApp / Telegram bot interface
- [ ] Vinted connector
- [ ] eBay connector
- [ ] Buycycle connector
- [ ] Automated reminders and action notifications

## Testing Strategy

### Test Layers

| Layer | What | How | When |
|---|---|---|---|
| **Unit** | Connectors, parsers, price calculations, funnel math | pytest with fixtures and mocked HTTP | Every commit |
| **Integration** | Full scrape → store → analyze pipeline | pytest against saved HTML snapshots (no live calls) | Every commit |
| **Live smoke** | Connector against real marketplace | pytest marker `@pytest.mark.live`, skipped in CI | Manual / weekly |
| **LLM evaluation** | Recommendation quality, listing text quality | Golden-set test cases with expected outcome ranges | Per model/prompt change |
| **End-to-end** | Full workflow: scrape → analyze → recommend → UI display | Streamlit app test with seeded DB | Pre-release |

### Key Flows to Test

```
1. SCRAPE FLOW:     Search query → httpx → Parse __NEXT_DATA__ SSR → Structured data → DB
2. MARKET ANALYSIS: DB snapshots → Price distribution → Competitor ranking → Insights
3. LISTING GEN:     Item + Market data → Claude API → Title/Description/Price
4. DAILY TRACK:     Cron trigger → Scrape own listings → Store metrics → Compute deltas
5. RECOMMENDATION:  Metrics history + Market data → Claude API → Prioritized actions
6. BUY EVALUATION:  Listing URL → Scrape → Market comparison → Fair value + Seller profile
7. NEGOTIATION:     Counterparty profile + Market data → Claude API → Strategy + Messages
```

### Connector Testing (Marketplace Snapshots)

Each connector ships with saved HTML/JSON snapshots in `tests/fixtures/`. This allows:
- Testing parser logic without hitting live sites
- Detecting when a marketplace changes their HTML structure (run live smoke test, compare)
- Reproducible tests across environments

### LLM Output Evaluation

LLM-generated content (listings, recommendations, negotiation strategies) is non-deterministic. Testing approach:
- **Golden test cases**: known item + known market data → expected recommendation category (not exact text)
- **Structural validation**: output matches expected schema (has price, has title, has reasoning)
- **Human feedback loop**: see Data Quality section below

## Data Quality & Feedback Loop

The agent improves over time by tracking what actually works.

### Outcome Tracking

Every recommendation and action is logged with a before/after:

```
Action Log Entry:
  item: "MacBook Pro 13"
  action: "lower_price"
  old_price: 169
  new_price: 149
  metrics_before: { views: 45, favs: 2, messages: 0 }
  metrics_after_24h: { views: 72, favs: 5, messages: 1 }
  metrics_after_72h: { views: 110, favs: 8, messages: 3 }
  outcome: "sold" | "no_change" | "improved_engagement"
```

### Feedback Signals

| Signal | Source | Feeds into |
|---|---|---|
| Action → Outcome | Metric deltas after action | Recommendation engine weights |
| Listing text → Conversion | Views/favs/messages after publish | Listing optimizer prompt tuning |
| Price → Time-to-sell | Days from listing to sale at given price | Price suggestion model |
| Negotiation strategy → Deal closed | Accepted price vs. initial offer | Negotiation strategy selection |
| User override | Juan rejects a recommendation | Negative signal for that pattern |

### Feedback Integration

- Phase 1-2: Store all outcomes, surface them in dashboard for manual review
- Phase 3+: Feed outcome data as context to Claude API calls ("in the past, lowering price by 10% on items with >50 views and 0 messages led to sale within 3 days in 4 out of 5 cases")
- Long-term: Build a scoring model from accumulated outcome data

## Security

### Principles

- **No PII storage** — the agent does not store personal information about buyers, sellers, or platform users. Counterparty profiles contain only behavioral signals (response speed, negotiation style) and public marketplace data (rating, number of sales)
- **Credentials encrypted at rest** — all marketplace API keys, tokens, and session cookies are stored encrypted, never in plaintext config files
- **Secrets management** — credentials flow through environment variables or an encrypted keyring, never committed to git

### Credential Handling

| What | Where | How |
|---|---|---|
| Marketplace tokens/cookies | OS keyring (macOS Keychain) | `keyring` Python library, accessed at runtime |
| Claude API key | Environment variable | `AWS_PROFILE` via Bedrock (no raw API key needed) |
| Session state | In-memory only | Marketplace sessions not persisted to disk |

### Data Classification

| Data Type | Stored? | Sensitivity | Notes |
|---|---|---|---|
| Listing metadata (prices, titles, photos) | Yes | Public | Scraped from public marketplace pages |
| Market snapshots (competitor prices) | Yes | Public | Aggregated, no user attribution |
| Own inventory and metrics | Yes | Low | User's own listing performance |
| Action log and outcomes | Yes | Low | User's own decisions and results |
| Counterparty behavioral signals | Yes | Low | Public profile data + interaction patterns, no names/IDs stored |
| Marketplace credentials | Encrypted | High | OS keyring, never in DB or git |
| Chat messages with buyers/sellers | No | Medium | Processed in-memory for negotiation, not persisted |

### .gitignore Enforcement

The `.gitignore` already excludes `.env`, `data/*.db`, and `data/*.json`. Additionally:
- Pre-commit hook validates no secrets in staged files
- No credential values in any config file — only references to env vars or keyring entries

## Current Inventory (Migration from Copilot)

| Item | Price | Posted | Marketplace |
|---|---|---|---|
| MacBook Pro 13" (2009) | 169 | Feb 25 | Wallapop |
| iMac 21.5" (Late 2013) | 239 | Mar 2 | Wallapop |
| iMac 20" (Early 2009, 1TB SSD+HDD) | 129 | Mar 3 | Wallapop |
| iPad 4 (A1458) 16GB | 35 | Mar 3 | Wallapop |
| iPad 2 (A1396) 32GB | 40 | Mar 3 | Wallapop |
