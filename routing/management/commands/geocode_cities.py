import csv
import io
import json
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from geopy.geocoders import Nominatim
import requests


CENSUS_BATCH_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"


class Command(BaseCommand):
    help = "Geocode unique city+state pairs (fast via US Census, fallback Nominatim)"

    def handle(self, *args, **kwargs):
        base = Path(__file__).resolve().parents[2] / "data"
        src = base / "cleaned_fuel_prices.csv"
        cache_file = base / "city_coords.json"

        if not src.exists():
            self.stdout.write(self.style.ERROR(f"Missing {src}. Run clean_csv first."))
            return

        if cache_file.exists():
            cache = json.loads(cache_file.read_text())
        else:
            cache = {}

        with open(src, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        unique = sorted({(r["city"], r["state"]) for r in rows})
        to_do = [(c, s) for (c, s) in unique if f"{c}|{s}" not in cache]

        self.stdout.write(
            f"Total unique cities: {len(unique)}. Need to geocode: {len(to_do)}"
        )

        if not to_do:
            self.stdout.write(self.style.SUCCESS("All cached. Nothing to do."))
            return

        # ---- Try the US Census Batch Geocoder first (fast, one HTTP call) ----
        try:
            self._census_batch(to_do, cache, cache_file)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Census batch failed: {e}"))

        # ---- Fall back to Nominatim for any misses ----
        remaining = [(c, s) for (c, s) in to_do if not cache.get(f"{c}|{s}")]
        if remaining:
            self.stdout.write(
                f"Falling back to Nominatim for {len(remaining)} misses..."
            )
            geolocator = Nominatim(user_agent="fuel_route_api_assessment")
            for i, (city, state) in enumerate(remaining, 1):
                key = f"{city}|{state}"
                try:
                    loc = geolocator.geocode(
                        f"{city}, {state}, USA",
                        country_codes="us",
                        timeout=10,
                    )
                    cache[key] = (
                        [loc.latitude, loc.longitude] if loc else None
                    )
                except Exception:
                    cache[key] = None
                time.sleep(1.1)
                if i % 25 == 0:
                    cache_file.write_text(json.dumps(cache, indent=2))
                    self.stdout.write(f"  ...progress: {i}/{len(remaining)}")

        cache_file.write_text(json.dumps(cache, indent=2))
        hits = sum(1 for v in cache.values() if v)
        misses = sum(1 for v in cache.values() if not v)
        self.stdout.write(self.style.SUCCESS(
            f"Done. Hits: {hits}, Misses: {misses}, Total cached: {len(cache)}"
        ))

    # ------------------------------------------------------------------
    def _census_batch(self, city_pairs, cache, cache_file):
        """Send all cities to the Census Batch Geocoder in one request."""
        # Build the batch CSV (ID, Street, City, State, ZIP)
        lines = ["ID,Street,City,State,ZIP"]
        for i, (city, state) in enumerate(city_pairs):
            city_clean = city.replace(",", "").strip()
            lines.append(f"{i},,{city_clean},{state},")
        batch_csv = "\n".join(lines).encode("utf-8")

        self.stdout.write(
            f"Sending {len(city_pairs)} rows to US Census batch API..."
        )

        r = requests.post(
            CENSUS_BATCH_URL,
            files={"addressFile": ("batch.csv", batch_csv, "text/csv")},
            data={
                "benchmark": "Public_AR_Current",
                "vintage": "Current_Current",
            },
            timeout=180,
        )
        r.raise_for_status()

        # Response is CSV
        reader = csv.reader(io.StringIO(r.text))
        hits = 0
        for row in reader:
            if len(row) < 5:
                continue
            try:
                idx = int(row[0])
            except (ValueError, IndexError):
                continue
            match_indicator = row[1].strip() if len(row) > 1 else ""
            if match_indicator.lower() != "match":
                continue
            coords = row[4].strip() if len(row) > 4 else ""
            if "," not in coords:
                continue
            try:
                lon_str, lat_str = coords.split(",")
                lat = float(lat_str.strip())
                lon = float(lon_str.strip())
            except ValueError:
                continue
            if 0 <= idx < len(city_pairs):
                c, s = city_pairs[idx]
                cache[f"{c}|{s}"] = [lat, lon]
                hits += 1

        cache_file.write_text(json.dumps(cache, indent=2))
        self.stdout.write(self.style.SUCCESS(
            f"Census batch returned {hits} matches out of {len(city_pairs)}"
        ))