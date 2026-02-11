# Tennis Data API Comparison Report

## Context

You're currently using **AllSportsAPI** (`allsportsapi2.p.rapidapi.com`) via RapidAPI for live tennis data. It powers `src/tennis/client.py` and `testing/tennis_data.py` with live match listing, point-by-point data, and Kalshi ticker matching. You asked for an evaluation of three alternative APIs to potentially replace or supplement it.

---

## Current Implementation: AllSportsAPI

| Attribute | Value |
|---|---|
| **Host** | `allsportsapi2.p.rapidapi.com` |
| **Free tier** | 100 calls/day |
| **Endpoints used** | `/api/tennis/events/live`, `/api/tennis/event/{id}`, `/api/tennis/event/{id}/point-by-point` |
| **Data provided** | Live matches, set scores, game scores (point field), player names/nameCodes, tournament info, serving indicator (`firstToServe`), match status, `changeTimestamp` for staleness detection |
| **Latency** | Standard REST (~200-500ms per call, typical RapidAPI) |
| **Point-by-point** | Yes (dedicated endpoint) |
| **Implementation complexity** | Simple -- 3 endpoints, clean JSON with `homeTeam`/`awayTeam`/`homeScore`/`awayScore` structure |

**Strengths:** Clean SofaScore-style response schema, point-by-point data, serving indicator, `nameCode` for Kalshi ticker matching, rate-limit headers exposed.

**Weaknesses:** 100 calls/day free tier is very tight for live polling (30s polling = 120 calls/hr = burns through daily limit in <1 hour).

---

## API 1: FlashScore4

| Attribute | Value |
|---|---|
| **Host** | `flashscore4.p.rapidapi.com` |
| **Source** | Third-party wrapper around FlashScore (no official FlashScore API exists) |
| **Free tier** | Unknown -- RapidAPI page doesn't expose pricing clearly; likely similar 500-1000 req/month free |
| **Documentation** | **Poor** -- no publicly accessible endpoint list; must use RapidAPI playground to discover |
| **Tennis coverage** | ATP, WTA, Grand Slams confirmed via FlashScore platform |
| **Expected data** | Live scores, match details, statistics (aces, double faults, etc.), H2H, odds |
| **Point-by-point** | **Unclear** -- FlashScore website shows point-by-point, but whether the API exposes it is undocumented |

### Assessment

| Criteria | Rating | Notes |
|---|---|---|
| Documentation quality | **Poor** | No public endpoint docs; reverse-engineering required |
| Tennis data depth | **Medium-High** | FlashScore platform is rich, but API coverage unknown |
| Point-by-point | **Uncertain** | Critical gap -- can't confirm without testing |
| Free tier generosity | **Unknown** | Can't evaluate without account |
| Implementation effort | **High** | Unknown schema, undocumented endpoints, trial-and-error needed |
| Maintenance burden | **High** | Unofficial wrapper; could break if FlashScore changes |
| Latency | **Standard** | RapidAPI proxy, similar to current |

**Verdict: Not recommended.** Too many unknowns. No documented endpoints, unofficial wrapper that could break, and no confirmation of point-by-point tennis data via the API. The effort to even evaluate it properly is high.

---

## API 2: Free Livescore API (Creativesdev)

| Attribute | Value |
|---|---|
| **Host** | `free-livescore-api.p.rapidapi.com` (estimated) |
| **Source** | Creativesdev on RapidAPI |
| **Free tier** | **Truly free** -- no credit card, no paid tiers |
| **Rate limits** | Unknown specific limit (general RapidAPI free: ~1000 req/hr) |
| **Documentation** | **Poor** -- multi-sport API, tennis endpoints not specifically documented |
| **Tennis coverage** | Listed as supported sport, but depth unclear |
| **Expected data** | Live scores, fixtures, standings, match events, statistics |
| **Point-by-point** | **Very unlikely** -- livescore APIs typically only provide score-level data |

### Assessment

| Criteria | Rating | Notes |
|---|---|---|
| Documentation quality | **Poor** | No tennis-specific endpoint docs |
| Tennis data depth | **Low** | Likely just live scores, not granular tennis data |
| Point-by-point | **Very unlikely** | Livescore APIs focus on score summaries |
| Free tier generosity | **Excellent** | Completely free |
| Implementation effort | **Medium** | Unknown schema but simple REST |
| Maintenance burden | **Medium** | Free APIs can disappear or degrade without notice |
| Latency | **Standard** | RapidAPI proxy |
| Serving indicator | **Very unlikely** | Granular tennis data (serve, game score) probably absent |

**Verdict: Not recommended for this use case.** While the price is right (free), livescore APIs are typically football-focused with tennis as an afterthought. For HFT-style prediction market trading, you need point-by-point data, serving indicators, and game-level scores -- a generic livescore API almost certainly won't provide this. The lack of documentation makes it impossible to verify without trial-and-error.

**Potential use:** Could work as a *fallback* source for just confirming which matches are live, but not as a primary data source.

---

## API 3: SofaScore6

| Attribute | Value |
|---|---|
| **Host** | `sofascore6.p.rapidapi.com` (estimated) |
| **Source** | Unofficial wrapper around SofaScore (SofaScore explicitly states they don't offer API access) |
| **Free tier** | **500 requests/month** (BASIC plan) |
| **Pro tier** | $5/mo -- 30,000 req/mo, 60 req/min |
| **Ultra tier** | $20/mo -- 300,000 req/mo, 120 req/min |
| **Mega tier** | $40/mo -- 1,000,000 req/mo, 180 req/min |

### Expected Endpoints (based on SofaScore ecosystem)

| Endpoint | Purpose |
|---|---|
| `GET /sport/tennis/events/live` | All live tennis events |
| `GET /event/{eventId}` | Match details |
| `GET /event/{eventId}/incidents` | Match incidents/events timeline |
| `GET /event/{eventId}/graph` | Momentum graph data |
| `GET /sport/tennis/scheduled-events/{date}` | Upcoming matches |
| `GET /unique-tournament/{id}/seasons` | Tournament season data |
| `GET /sport/tennis/categories` | All tennis categories |

### Assessment

| Criteria | Rating | Notes |
|---|---|---|
| Documentation quality | **Medium** | Not great, but SofaScore's schema is well-known from web scraping community |
| Tennis data depth | **High** | SofaScore is one of the richest tennis data sources |
| Point-by-point | **Likely yes** | SofaScore website shows point-by-point; API likely exposes via incidents endpoint |
| Free tier generosity | **Poor** | 500 req/month = ~16/day, unusable for polling |
| Paid tier value | **Good** | $5/mo for 30K req = 1000/day, much better than AllSportsAPI's 100/day |
| Implementation effort | **Low-Medium** | Schema very similar to AllSportsAPI (SofaScore-style `homeTeam`/`awayTeam`/`homeScore`/`awayScore`) |
| Maintenance burden | **Medium-High** | Unofficial wrapper; SofaScore actively blocks scrapers |
| Latency | **Standard** | RapidAPI proxy |
| Serving indicator | **Likely yes** | SofaScore tracks serving player |

**Verdict: Best alternative, but with caveats.** SofaScore has the richest tennis data of all options. The response schema is likely very similar to your current AllSportsAPI (both use SofaScore-style structures with `homeTeam`/`awayTeam`, `homeScore`/`awayScore`, `firstToServe`). This means migration would be straightforward -- mostly changing the base URL and adjusting a few field names.

**However:** The free tier (500 req/month) is useless for live polling. You'd need at minimum the $5/mo Pro tier. And since SofaScore doesn't officially offer API access, this wrapper could break at any time.

---

## Head-to-Head Comparison

| Feature | AllSportsAPI (current) | FlashScore4 | Free Livescore | SofaScore6 |
|---|---|---|---|---|
| **Free tier** | 100/day | Unknown | Unlimited | 500/month |
| **Best paid tier for this use** | N/A | Unknown | Free | $5/mo (30K/mo) |
| **Tennis depth** | High | Unknown | Low | Very High |
| **Point-by-point** | Yes | Uncertain | Very unlikely | Likely yes |
| **Serving indicator** | Yes | Unknown | Very unlikely | Likely yes |
| **Game-level scores** | Yes | Unknown | Unlikely | Yes |
| **Kalshi matching** | Yes (nameCode) | Unknown | Unknown | Likely (similar schema) |
| **Documentation** | Decent | Poor | Poor | Medium |
| **Schema similarity** | -- | Unknown | Different | Very similar |
| **Implementation effort** | -- | High | Medium | Low |
| **Reliability** | Good | Risky | Risky | Medium |
| **Migration effort** | -- | Full rewrite | Full rewrite | Minimal changes |

---

## Recommendation

### Keep AllSportsAPI as primary, consider SofaScore6 Pro ($5/mo) as upgrade path

**Reasoning:**

1. **AllSportsAPI is working well** -- you already have a clean implementation with all the features you need (live matches, point-by-point, serving indicator, Kalshi ticker matching). The 100/day limit is the only pain point.

2. **SofaScore6 is the only viable alternative** of the three evaluated. It has the data depth you need and a schema similar enough that migration would be minimal. But:
   - Free tier is useless (500/month)
   - It's an unofficial wrapper that could break
   - You'd be paying $5/mo minimum

3. **FlashScore4 and Free Livescore are not suitable** -- FlashScore4 has zero usable documentation, and Free Livescore almost certainly lacks tennis-granular data.

### If you want to proceed with SofaScore6

The implementation would be straightforward -- the changes to `src/tennis/client.py` would be:
- Change `BASE_URL` to the SofaScore6 host
- Change `x-rapidapi-host` header
- Adjust endpoint paths (e.g., `/sport/tennis/events/live` instead of `/api/tennis/events/live`)
- Minor field name adjustments if any
- Update rate limit constants (DAILY_LIMIT etc.)

`testing/tennis_data.py` would need zero changes since it uses the `TennisClient` abstraction.

### If you want maximum free-tier capacity

Stick with AllSportsAPI (100/day) and be strategic about polling intervals. At 60s intervals, a 1-hour live session = 60 calls = manageable.
