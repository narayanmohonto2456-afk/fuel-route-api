import csv
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from routing.models import FuelStation


class Command(BaseCommand):
    help = "Load cleaned fuel prices + coordinates into the DB"

    def handle(self, *args, **kwargs):
        base = Path(__file__).resolve().parents[2] / "data"
        csv_path = base / "cleaned_fuel_prices.csv"
        coords_path = base / "city_coords.json"

        if not csv_path.exists():
            self.stdout.write(self.style.ERROR(
                f"Missing {csv_path}. Run 'clean_csv' first."
            ))
            return
        if not coords_path.exists():
            self.stdout.write(self.style.ERROR(
                f"Missing {coords_path}. Run 'geocode_cities' first."
            ))
            return

        coords = json.loads(coords_path.read_text())

        FuelStation.objects.all().delete()

        batch = []
        loaded = 0
        skipped = 0

        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = f"{r['city']}|{r['state']}"
                c = coords.get(key)
                if not c:
                    skipped += 1
                    continue
                batch.append(FuelStation(
                    opis_id=int(r["opis_id"]),
                    name=r["name"],
                    address=r["address"],
                    city=r["city"],
                    state=r["state"],
                    latitude=c[0],
                    longitude=c[1],
                    price_per_gallon=float(r["price"]),
                ))
                if len(batch) >= 1000:
                    FuelStation.objects.bulk_create(batch)
                    loaded += len(batch)
                    batch = []

        if batch:
            FuelStation.objects.bulk_create(batch)
            loaded += len(batch)

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {loaded} stations. Skipped {skipped} (no coords)."
        ))