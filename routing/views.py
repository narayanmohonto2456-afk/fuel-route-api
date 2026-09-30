import os
import uuid
import folium
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema

from .serializers import RouteRequestSerializer
from .services.geocoding import geocode
from .services.routing import get_route
from .services.fuel_optimizer import stations_along_route, optimize_fuel_plan


class RouteFuelView(APIView):

    @extend_schema(request=RouteRequestSerializer)
    def post(self, request):
        ser = RouteRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        start_raw = ser.validated_data["start"]
        finish_raw = ser.validated_data["finish"]

        try:
            start_coords = self._resolve(start_raw)
            finish_coords = self._resolve(finish_raw)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = get_route(start_coords, finish_coords)
        except Exception as e:
            return Response({"error": f"Routing failed: {e}"},
                            status=status.HTTP_502_BAD_GATEWAY)

        try:
            nearby = stations_along_route(route["geometry"], route["distance_miles"])
            plan = optimize_fuel_plan(nearby, route["distance_miles"])
        except ValueError as e:
            return Response({"error": str(e)},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        map_url = self._build_map(
            route["geometry"], plan["stops"], start_coords, finish_coords
        )

        return Response({
            "start": start_raw,
            "finish": finish_raw,
            "start_coords": start_coords,
            "finish_coords": finish_coords,
            "distance_miles": round(route["distance_miles"], 2),
            "duration_minutes": round(route["duration_seconds"] / 60, 1),
            "route_geometry": [[lat, lon] for lat, lon in route["geometry"][::5]],
            "fuel_stops": plan["stops"],
            "total_cost_usd": plan["total_cost"],
            "total_gallons_used": plan["total_gallons"],
            "mpg": plan["mpg"],
            "max_range_miles": plan["max_range_miles"],
            "map_url": request.build_absolute_uri(map_url),
        }, status=status.HTTP_200_OK)

    @staticmethod
    def _resolve(raw):
        if "," in raw:
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) == 2:
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        return (lat, lon)
                except ValueError:
                    pass
        return geocode(raw)

    @staticmethod
    def _build_map(geometry, stops, start_coords, finish_coords):
        sampled = geometry[::5]
        centre = sampled[len(sampled) // 2]

        m = folium.Map(location=centre, zoom_start=5, tiles="OpenStreetMap")
        folium.PolyLine(sampled, color="#1f77b4", weight=4, opacity=0.85).add_to(m)

        folium.Marker(start_coords, popup="Start",
                      icon=folium.Icon(color="green", icon="play")).add_to(m)
        folium.Marker(finish_coords, popup="Finish",
                      icon=folium.Icon(color="red", icon="stop")).add_to(m)

        for i, s in enumerate(stops, 1):
            if s.get("lat") and s.get("lon"):
                folium.Marker(
                    [s["lat"], s["lon"]],
                    popup=(
                        f"<b>Stop {i}: {s['station']}</b><br>"
                        f"{s['city']}, {s['state']}<br>"
                        f"${s['price_per_gallon']}/gal<br>"
                        f"{s['gallons']} gal &rarr; ${s['cost']}"
                    ),
                    icon=folium.Icon(color="orange", icon="gas-pump", prefix="fa"),
                ).add_to(m)

        map_dir = os.path.join(settings.MEDIA_ROOT, "maps")
        os.makedirs(map_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.html"
        m.save(os.path.join(map_dir, fname))
        return f"{settings.MEDIA_URL}maps/{fname}"