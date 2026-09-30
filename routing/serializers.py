from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=300)
    finish = serializers.CharField(max_length=300)

    def validate_start(self, v):
        return self._v(v)

    def validate_finish(self, v):
        return self._v(v)

    @staticmethod
    def _v(v):
        v = v.strip()
        if not v:
            raise serializers.ValidationError("Location required.")
        return v


class FuelStopSerializer(serializers.Serializer):
    station = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    lat = serializers.FloatField(allow_null=True)
    lon = serializers.FloatField(allow_null=True)
    dist_miles = serializers.FloatField()
    price_per_gallon = serializers.FloatField()
    gallons = serializers.FloatField()
    cost = serializers.FloatField()