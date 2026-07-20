from tortoise import fields
from tortoise.models import Model


class WeatherLookup(Model):
    id = fields.IntField(primary_key=True)
    city = fields.CharField(max_length=120)
    country_code = fields.CharField(max_length=2)
    location_name = fields.CharField(max_length=120)
    location_country = fields.CharField(max_length=120, null=True)
    location_timezone = fields.CharField(max_length=120, null=True)
    latitude = fields.FloatField()
    longitude = fields.FloatField()
    weather = fields.JSONField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "weather_lookups"
        ordering = ["-created_at"]
