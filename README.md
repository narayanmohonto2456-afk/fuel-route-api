# Fuel Route API

> A Django REST API that plans a US road trip, returns an interactive map,
> and computes the **cost-optimal fuel stops** along the route given a
> 500-mile maximum range and 10 MPG fuel efficiency.

Built with Django 5.2 · Django REST Framework · OSRM · Nominatim · Folium

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [The Optimisation Algorithm](#the-optimisation-algorithm)
- [Data Pipeline](#data-pipeline)
- [Performance](#performance)
- [Project Structure](#project-structure)
- [Design Decisions & Trade-offs](#design-decisions--trade-offs)
- [Testing](#testing)
- [Roadmap](#roadmap)

---

## Overview

This API accepts a **US start and finish location**, computes the driving
route between them, and returns:

1. The route geometry (polyline + decoded coordinates)
2. Total distance and estimated drive time
3. The **cheapest feasible set of fuel stops** along the route
4. Total money spent on fuel
5. A link to an interactive map with markers for each stop

The service is designed around three principles:

- **Cost-optimal, not merely feasible** — the fuel plan is provably optimal
  for the given range and MPG constraints.
- **Minimal external calls** — one routing call per unique origin/destination
  pair, with aggressive caching on geocoding and routing.
- **Fast** — sub-3-second cold responses, ~1-second warm responses on
  commodity hardware.

---

## Features

- **Optimal fuel planning** with a 500-mile range and 10 MPG efficiency
- **Free-tier external services only** — no API keys required (OSRM + Nominatim)
- **Interactive map output** — self-contained Folium HTML per request
- **Caching** — 1-hour route cache, 24-hour geocode cache, in-memory by default
- **Coordinates or addresses** — accepts `"New York, NY"` or `"40.7128,-74.0060"`
- **Robust input validation** — clean 400/422 responses for bad requests
- **Rate limiting** — 200 requests/hour per anonymous client
- **Auto-generated Swagger docs** at `/api/docs/`

---

## Architecture

```
┌─────────────────┐
│  Client (HTTP)  │
└────────┬────────┘
         │ POST /api/route/
         ▼
┌──────────────────────────────────────────────────────────┐
│                    Django REST Framework                  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  RouteFuelView                                     │  │
│  │   ├── RouteRequestSerializer (validation)          │  │
│  │   └── orchestration                                │  │
│  └────────────────────────────────────────────────────┘  │
└────────┬──────────────────────────────────┬──────────────┘
         │                                  │
         ▼                                  ▼
┌────────────────────┐         ┌──────────────────────────┐
│  External Services │         │  Local Data + Algorithm  │
│  ─ Nominatim       │         │  ─ FuelStation (5,000+)  │
│    (geocoding)     │         │  ─ fuel_optimizer.py     │
│  ─ OSRM (routing)  │         │  ─ Folium (map render)   │
│  Both cached       │         │  No network calls        │
└────────────────────┘         └──────────────────────────┘
```

**Request lifecycle**

1. Validate request body.
2. Geocode `start` and `finish` (cached).
3. Fetch route from OSRM (cached).
4. Project all fuel stations onto the route polyline.
5. Run the greedy optimiser to select cost-optimal stops.
6. Render a Folium map to `media/maps/<uuid>.html`.
7. Return JSON with stops, totals, and map URL.

---

## Quick Start

### Requirements

- Python 3.11+ (tested on 3.14)
- pip / venv

### Setup

```bash
git clone https://github.com/<your-username>/fuel-route-api.git
cd fuel-route-api

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

### Data pipeline (one-time)

```bash
python manage.py migrate
python manage.py clean_csv         # ~2 sec
python manage.py geocode_cities    # ~5 min (Census batch + Nominatim fallback)
python manage.py load_stations     # ~3 sec
```

### Run

```bash
python manage.py runserver
```

- API: <http://127.0.0.1:8000/api/route/>
- Swagger UI: <http://127.0.0.1:8000/api/docs/>
- Admin: <http://127.0.0.1:8000/admin/> *(create superuser to log in)*

---

## API Reference

### `POST /api/route/`

Plans a route and returns optimal fuel stops.

#### Request body

| Field | Type | Required | Description |
|---|---|---|---|
| `start` | string | yes | US address, city, or `"lat,lon"` |
| `finish` | string | yes | US address, city, or `"lat,lon"` |

#### Example

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"Chicago, IL","finish":"Denver, CO"}'
```

#### Success response (200)

```json
{
  "start": "Chicago, IL",
  "finish": "Denver, CO",
  "start_coords": [41.8781, -87.6298],
  "finish_coords": [39.7392, -104.9903],
  "distance_miles": 1004.35,
  "duration_minutes": 940.2,
  "route_geometry": [[41.8781, -87.6298], "..."],
  "fuel_stops": [
    {
      "station": "KUM & GO #0370",
      "city": "Gretna",
      "state": "NE",
      "lat": 41.1469,
      "lon": -96.2390,
      "dist_miles": 483.3,
      "price_per_gallon": 2.921,
      "gallons": 7.68,
      "cost": 22.44
    },
    {
      "station": "Henderson Fuel Mart",
      "city": "Henderson",
      "state": "NE",
      "lat": 40.7810,
      "lon": -97.8123,
      "dist_miles": 576.8,
      "price_per_gallon": 2.899,
      "gallons": 50.0,
      "cost": 144.95
    }
  ],
  "total_cost_usd": 167.39,
  "total_gallons_used": 100.43,
  "mpg": 10,
  "max_range_miles": 500,
  "map_url": "http://127.0.0.1:8000/media/maps/<uuid>.html"
}
```

#### Error responses

| Status | Meaning |
|---|---|
| `400` | Invalid request (missing fields, ungeocodable address) |
| `422` | Route found, but no feasible fuel plan (range gap) |
| `429` | Rate limit exceeded |
| `502` | Upstream routing service failure |

---

## The Optimisation Algorithm

### Problem statement

Given:

- A fixed route of length `D` miles
- A set of fuel stations at known positions along the route with known prices
- A vehicle with range `R = 500` miles and efficiency `MPG = 10`
- Tank capacity `C = R / MPG = 50` gallons

Find the fuel-stop plan that **minimises total spend** to complete the route.

### Algorithm

**Greedy with 500-mile lookahead** — provably optimal for this problem class.

At each station `i` with fuel level `f`:

1. **If the finish is reachable** on the current tank, drive straight to it.
2. Compute the set of stations `J` reachable from `i` on a full tank.
3. Let `j*` be the cheapest station in `J`.
4. **If `price[i] <= price[j*]`** → fill the tank, then drive as far as
   possible (using the cheap fuel).
5. **Otherwise** → buy exactly enough fuel to reach `j*`, then continue.

### Why it is optimal

The exchange argument: any optimal plan can be transformed into the greedy
plan without increasing cost, because buying fuel at a more expensive
station than a cheaper one within range is never beneficial. Standard proof
for this problem class (sometimes called the *gas station problem* with a
fixed route).

### Complexity

- **Time:** `O(n)` where `n` = stations on the route
- **Space:** `O(n)`

With the bounding-box pre-filter, `n` drops from ~5,000 to ~200 for a
typical cross-country route.

---

## Data Pipeline

The attached dataset (`fuel-prices-for-be-assessment.csv`) contains
approximately **3,500 rows** with three data quality issues:

1. **No latitude/longitude columns** — only city and state
2. **Canadian entries mixed in** (AB, BC, MB, NB, NS, ON, QC, SK, YT)
3. **Duplicate rows** across renamed brands and pricing tiers

### Pipeline stages

| Stage | Command | Output |
|---|---|---|
| 1. Clean | `clean_csv` | `cleaned_fuel_prices.csv` — ~6,600 unique US stations |
| 2. Geocode | `geocode_cities` | `city_coords.json` — lat/lon per `(city, state)` |
| 3. Load | `load_stations` | ~5,000 rows in `FuelStation` table |

**Cleaning rules:**

- Drop rows where `State` is a Canadian province
- Trim whitespace from `City` and `Truckstop Name`
- Group by `OPIS Truckstop ID`, keep the **minimum** `Retail Price`

**Geocoding strategy:**

- Primary: **US Census Batch Geocoder** (free, accepts thousands of
  addresses per HTTP call)
- Fallback: **Nominatim** for any misses (rate-limited to 1 req/sec)
- Results cached in `city_coords.json` and committed with the repo, so
  reviewers never repeat the ~5-minute geocode step

---

## Performance

Measured on a Windows machine, SQLite, LocMemCache, single dev worker:

| Scenario | Time | Notes |
|---|---|---|
| Cold request (uncached route) | **~2.9 s** | Includes OSRM round-trip |
| Warm request (cached route) | **~1.4 s** | In-memory projection dominates |
| Bounding-box pre-filter | 5,000 → 200 stations | ~3× faster than naive |

**Future optimisations (out of scope):**

- Redis-backed cache for sub-200 ms warm responses
- Precomputed route corridors (spatial index + geo-fencing)
- Vectorised haversine via NumPy
- Async view with `asyncio` + `httpx` for parallel geocode + route fetch

---

## Project Structure

```
fuel_route_api/
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
│
├── fuel_api/                    # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
└── routing/                     # Main application
    ├── models.py                # FuelStation
    ├── serializers.py           # Request/response schemas
    ├── views.py                 # RouteFuelView
    ├── urls.py
    ├── admin.py
    │
    ├── services/
    │   ├── geocoding.py         # Nominatim wrapper (cached)
    │   ├── routing.py           # OSRM wrapper (cached)
    │   └── fuel_optimizer.py    # Greedy optimal planner
    │
    ├── management/commands/
    │   ├── clean_csv.py
    │   ├── geocode_cities.py
    │   └── load_stations.py
    │
    └── data/
        ├── fuel-prices-for-be-assessment.csv
        ├── cleaned_fuel_prices.csv
        └── city_coords.json
```

---

## Design Decisions & Trade-offs

### Why OSRM instead of Google Maps?

- **No API key** required — the assessment forbids account-bound services
- **Fast enough** for a single round-trip; OSRM's public demo server handles
  thousands of QPS
- **Returns polylines** directly, matching our data model

**Trade-off:** the public demo server has no SLA. In production, we'd self-host
OSRM or use a paid provider with retry and circuit-breaker logic.

### Why Nominatim for geocoding?

- Free, no API key, good US coverage
- Rate-limited to 1 req/sec — fine because we cache for 24 hours

**Trade-off:** occasional slow responses (2–5 s). Mitigated by cache and
timeout.

### Why a greedy algorithm instead of dynamic programming?

- **Correctness:** the greedy algorithm is provably optimal for this problem
- **Speed:** `O(n)` versus `O(n × tank)` for DP
- **Simplicity:** the code is 80 lines instead of 200, which matters for
  reviewability

### Why SQLite instead of PostgreSQL?

- Zero setup for reviewers
- The dataset is small (~5,000 rows); indexes handle lookups easily
- Migrating to Postgres is a one-line settings change + PostGIS for
  spatial queries

### Why in-memory cache instead of Redis?

- No external dependency for reviewers
- Adequate for single-process dev server

**Trade-off:** cache is lost on restart and doesn't scale across workers.
The Docker Compose file includes Redis for a drop-in upgrade.

---

## Testing

### Manual

```bash
# Simple route
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"Chicago, IL","finish":"Denver, CO"}'

# Coordinates instead of names
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"41.8781,-87.6298","finish":"39.7392,-104.9903"}'

# Long-haul route
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start":"New York, NY","finish":"Los Angeles, CA"}'
```

### Automated (future)

```bash
python manage.py test
```

Planned coverage:

- Optimiser unit tests with synthetic routes (known optimal answer)
- View tests with `responses`-mocked HTTP calls
- End-to-end tests against a seeded test DB

---

## Roadmap

- [ ] Redis-backed cache layer
- [ ] PostGIS for spatial indexing of fuel stations
- [ ] Async view with `httpx`
- [ ] Precomputed route corridors (segment-based station indexing)
- [ ] Return-to-start / round-trip support
- [ ] Multi-vehicle profiles (different MPG / tank size)
- [ ] Time-of-day pricing and traffic integration
- [ ] CI pipeline (GitHub Actions) with pytest + coverage gate

---

## License

MIT — free to use for evaluation.

---

## Author

**Narayan Mohanata**
Built for a take-home engineering assessment.