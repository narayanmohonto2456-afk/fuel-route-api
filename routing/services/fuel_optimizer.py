import math

MAX_RANGE_MILES = 500
MPG = 10
TANK_GAL = MAX_RANGE_MILES / MPG  # 50 gallons


def _haversine_mi(lat1, lon1, lat2, lon2):
    """Distance in miles between two lat/lon points."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def stations_along_route(route_geometry, total_miles, max_offset_mi=3.0):
    """
    Project every FuelStation in the DB onto the route polyline.
    Uses a bounding-box pre-filter for speed.
    """
    from routing.models import FuelStation

    # ---- 1. Build cumulative distance array along route ----
    cum = [0.0]
    for i in range(1, len(route_geometry)):
        d = _haversine_mi(*route_geometry[i - 1], *route_geometry[i])
        cum.append(cum[-1] + d)

    # ---- 2. Bounding box of the route (+~1 degree of buffer) ----
    lats = [p[0] for p in route_geometry]
    lons = [p[1] for p in route_geometry]
    buf = 1.0  # ~70 miles; covers max_offset_mi comfortably
    min_lat, max_lat = min(lats) - buf, max(lats) + buf
    min_lon, max_lon = min(lons) - buf, max(lons) + buf

    # ---- 3. Pre-filter stations via DB query (fast) ----
    candidates = FuelStation.objects.filter(
        latitude__gte=min_lat, latitude__lte=max_lat,
        longitude__gte=min_lon, longitude__lte=max_lon,
    ).values(
        "name", "latitude", "longitude",
        "price_per_gallon", "city", "state"
    )

    # ---- 4. Coarse route samples for nearest-point search ----
    step = 10
    samples = [(cum[i], route_geometry[i])
               for i in range(0, len(route_geometry), step)]

    results = []
    for s in candidates:
        best_d = float("inf")
        best_idx = 0

        for idx, (_, (lat, lon)) in enumerate(samples):
            d = _haversine_mi(s["latitude"], s["longitude"], lat, lon)
            if d < best_d:
                best_d = d
                best_idx = idx * step

        # Refine ±step around best
        lo = max(0, best_idx - step)
        hi = min(len(route_geometry), best_idx + step + 1)
        for i in range(lo, hi):
            d = _haversine_mi(
                s["latitude"], s["longitude"],
                route_geometry[i][0], route_geometry[i][1]
            )
            if d < best_d:
                best_d = d
                best_idx = i

        if best_d <= max_offset_mi:
            results.append({
                "name": s["name"],
                "lat": s["latitude"],
                "lon": s["longitude"],
                "price": float(s["price_per_gallon"]),
                "dist": cum[best_idx],
                "city": s["city"],
                "state": s["state"],
                "offset_mi": best_d,
            })

    # ---- 5. Dedupe by distance, keep cheapest ----
    by_dist = {}
    for r in results:
        k = round(r["dist"], 1)
        if k not in by_dist or r["price"] < by_dist[k]["price"]:
            by_dist[k] = r

    return sorted(by_dist.values(), key=lambda x: x["dist"])


def optimize_fuel_plan(stations, total_miles):
    """
    Greedy optimal refuelling with 500-mile lookahead.

    At each stop:
      - If current station is cheaper than every reachable station → fill tank,
        then drive as far as possible (using cheap fuel).
      - Otherwise → buy just enough to reach the next cheaper station.

    Returns dict with stops[], total_cost, total_gallons, total_miles.
    """
    # Build the timeline: start, then all stations, then finish
    points = [{
        "dist": 0.0, "price": None, "name": "Start",
        "lat": None, "lon": None, "city": "", "state": ""
    }]
    points += stations
    points.append({
        "dist": total_miles, "price": None, "name": "Finish",
        "lat": None, "lon": None, "city": "", "state": ""
    })

    # Feasibility: no gap between consecutive points may exceed MAX_RANGE
    for i in range(1, len(points)):
        gap = points[i]["dist"] - points[i - 1]["dist"]
        if gap > MAX_RANGE_MILES + 1e-6:
            raise ValueError(
                f"No fuel station within {MAX_RANGE_MILES} miles "
                f"(gap of {gap:.0f} mi near mile "
                f"{points[i - 1]['dist']:.0f})"
            )

    stops = []
    total_cost = 0.0
    fuel = TANK_GAL   # start with a full tank
    idx = 0
    n = len(points)
    finish_idx = n - 1

    while idx < finish_idx:
        cur = points[idx]

        # Can we reach the finish on the current tank?
        dist_finish = points[finish_idx]["dist"] - cur["dist"]
        if fuel * MPG >= dist_finish - 1e-6:
            fuel -= dist_finish / MPG
            break

        # Reachable stations within MAX_RANGE from here
        reachable = []
        for j in range(idx + 1, n):
            d = points[j]["dist"] - cur["dist"]
            if d > MAX_RANGE_MILES + 1e-6:
                break
            reachable.append(j)

        if not reachable:
            raise ValueError(
                f"Stuck at mile {cur['dist']:.0f}: no station within range."
            )

        # Cheapest real station reachable (ignore finish — it has no price)
        cheapest_j = None
        cheapest_price = float("inf")
        for j in reachable:
            p = points[j]["price"]
            if p is not None and p < cheapest_price:
                cheapest_price = p
                cheapest_j = j

        cur_price = cur["price"] if cur["price"] is not None else float("inf")

        # If nothing but the finish is reachable, head straight for it
        if cheapest_j is None:
            need = dist_finish / MPG
            if cur["price"] is not None:
                buy = max(0.0, need - fuel)
                if buy > 1e-6:
                    total_cost += buy * cur["price"]
                    stops.append({
                        "station": cur["name"],
                        "city": cur["city"],
                        "state": cur["state"],
                        "lat": cur["lat"],
                        "lon": cur["lon"],
                        "dist_miles": round(cur["dist"], 1),
                        "price_per_gallon": round(cur["price"], 3),
                        "gallons": round(buy, 2),
                        "cost": round(buy * cur["price"], 2),
                    })
                    fuel += buy
            fuel -= need
            idx = finish_idx
            continue

        if cur_price <= cheapest_price:
            # Current is cheapest in window → fill tank, drive as far as possible
            buy = TANK_GAL - fuel
            if cur_price != float("inf") and buy > 1e-6:
                total_cost += buy * cur_price
                stops.append({
                    "station": cur["name"],
                    "city": cur["city"],
                    "state": cur["state"],
                    "lat": cur["lat"],
                    "lon": cur["lon"],
                    "dist_miles": round(cur["dist"], 1),
                    "price_per_gallon": round(cur_price, 3),
                    "gallons": round(buy, 2),
                    "cost": round(buy * cur_price, 2),
                })
                fuel += buy
            target = reachable[-1]   # farthest reachable
        else:
            # Buy just enough to reach the cheapest station ahead
            dist_target = points[cheapest_j]["dist"] - cur["dist"]
            need = dist_target / MPG
            if cur["price"] is not None:
                buy = max(0.0, need - fuel)
                if buy > 1e-6:
                    total_cost += buy * cur["price"]
                    stops.append({
                        "station": cur["name"],
                        "city": cur["city"],
                        "state": cur["state"],
                        "lat": cur["lat"],
                        "lon": cur["lon"],
                        "dist_miles": round(cur["dist"], 1),
                        "price_per_gallon": round(cur["price"], 3),
                        "gallons": round(buy, 2),
                        "cost": round(buy * cur["price"], 2),
                    })
                    fuel += buy
            target = cheapest_j

        # Drive to target
        dist_target = points[target]["dist"] - cur["dist"]
        fuel = max(0.0, fuel - dist_target / MPG)
        idx = target

    return {
        "stops": stops,
        "total_cost": round(total_cost, 2),
        "total_gallons": round(total_miles / MPG, 2),
        "total_miles": round(total_miles, 2),
        "max_range_miles": MAX_RANGE_MILES,
        "mpg": MPG,
    }