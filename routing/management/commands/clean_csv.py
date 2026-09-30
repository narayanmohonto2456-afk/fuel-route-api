import csv
from pathlib import Path
from django.core.management.base import BaseCommand

CANADIAN = {"AB", "BC", "MB", "NB", "NS", "ON", "QC", "SK", "YT", "NT", "NU", "PE", "NL"}


class Command(BaseCommand):
    help = "Clean the raw fuel prices CSV"

    def handle(self, *args, **kwargs):
        base = Path(__file__).resolve().parents[2] / "data"
        src = base / "fuel-prices-for-be-assessment.csv"
        dst = base / "cleaned_fuel_prices.csv"

        if not src.exists():
            self.stdout.write(self.style.ERROR(f"CSV not found at: {src}"))
            return

        rows = []
        with open(src, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                state = (r["State"] or "").strip().upper()
                if not state or state in CANADIAN:
                    continue
                rows.append({
                    "opis_id": int(r["OPIS Truckstop ID"]),
                    "name": r["Truckstop Name"].strip(),
                    "address": r["Address"].strip(),
                    "city": r["City"].strip(),
                    "state": state,
                    "price": float(r["Retail Price"]),
                })

        best = {}
        for r in rows:
            key = r["opis_id"]
            if key not in best or r["price"] < best[key]["price"]:
                best[key] = r

        cleaned = sorted(best.values(), key=lambda x: (x["state"], x["city"]))

        with open(dst, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["opis_id", "name", "address", "city", "state", "price"],
            )
            writer.writeheader()
            writer.writerows(cleaned)

        self.stdout.write(self.style.SUCCESS(
            f"Cleaned: {len(rows)} US rows -> {len(cleaned)} unique stations -> {dst}"
        ))