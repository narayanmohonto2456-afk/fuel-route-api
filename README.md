# Fuel Route API

Django 5.2 REST API that plans a US road trip, returns an interactive map of
the route, and identifies the **cost-optimal** fuel stops along the way,
assuming a 500-mile max range and 10 mpg fuel efficiency.

Built as a take-home assessment. Uses only free, key-less external services.

---

## Quick Start

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env

python manage.py migrate
python manage.py clean_csv        # cleans the attached CSV (~2s)
python manage.py geocode_cities   # offline geocoding (~5 min, once)
python manage.py load_stations    # loads ~5,000 US stations

python manage.py runserver