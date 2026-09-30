import requests
from django.core.cache import cache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def geocode(address: str):
    key = f"geo:{address.lower().strip().replace(' ', '_').replace(',', '_')}"
    cached = cache.get(key)
    if cached:
        return tuple(cached)

    params = {"q": address, "format": "json", "limit": 1, "countrycodes": "us"}
    r = requests.get(
        NOMINATIM_URL,
        params=params,
        headers={"User-Agent": "FuelRouteAPI/1.0"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"Could not geocode: {address}")

    result = (float(data[0]["lat"]), float(data[0]["lon"]))
    cache.set(key, result, 86400)
    return result