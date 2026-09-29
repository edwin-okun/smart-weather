from tortoise import fields
from tortoise.models import Model

from app.models.auth import ApiClient


class WeatherLookup(Model):
    id = fields.IntField(primary_key=True)
    # The API client that made the lookup; history is scoped to it. Nullable only
    # for rows written before lookups were client-scoped: those are visible to
    # nobody and age out through retention. CASCADE rather than SET_NULL because a
    # lookup is the owning client's data: a SET_NULL row could never be read again
    # (null means invisible), so keeping it would only retain search history for a
    # client that no longer exists.
    client: fields.ForeignKeyNullableRelation[ApiClient] = fields.ForeignKeyField(
        "models.ApiClient",
        related_name="weather_lookups",
        on_delete=fields.CASCADE,
        null=True,
    )
    city = fields.CharField(max_length=120)
    country_code = fields.CharField(max_length=2)
    location_name = fields.CharField(max_length=120)
    location_country = fields.CharField(max_length=120, null=True)
    location_timezone = fields.CharField(max_length=120, null=True)
    latitude = fields.FloatField()
    longitude = fields.FloatField()
    weather = fields.JSONField()
    # Indexed on its own for the retention prune, which spans all clients.
    created_at = fields.DatetimeField(auto_now_add=True, db_index=True)

    class Meta:
        table = "weather_lookups"
        ordering = ["-created_at"]
        # "Latest lookups for this client": equality on client_id, range and
        # ordering on created_at.
        indexes = (("client_id", "created_at"),)
