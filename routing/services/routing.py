import requests
import polyline
from django.core.cache import cache

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def get_route(origin, destination):
    key = f"route:{origin[0]:.4f},{origin[1]:.4f}:{destination[0]:.4f},{destination[1]:.4f}"
    cached = cache.get(key)
    if cached:
        return cached

    coords = f"{origin[1]},{origin[0]};{destination[1]},{destination[0]}"
    params = {"overview": "full", "geometries": "polyline", "steps": "false"}
    r = requests.get(f"{OSRM_URL}/{coords}", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    if data.get("code") != "Ok":
        raise ValueError(f"OSRM: {data.get('message', 'routing failed')}")

    route = data["routes"][0]
    geometry = polyline.decode(route["geometry"])
    result = {
        "geometry": geometry,
        "distance_miles": route["distance"] / 1609.344,
        "duration_seconds": route["duration"],
    }
    cache.set(key, result, 3600)
    return result