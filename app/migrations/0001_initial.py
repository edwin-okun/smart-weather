from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.fields.base import OnDelete
from tortoise.fields.data import JSON_DUMPS, JSON_LOADS
from tortoise import fields

class Migration(migrations.Migration):
    initial = True

    operations = [
        ops.CreateModel(
            name='ApiClient',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('client_id', fields.CharField(unique=True, db_index=True, max_length=80)),
                ('client_secret_hash', fields.CharField(max_length=255)),
                ('name', fields.CharField(max_length=120)),
                ('scopes', fields.JSONField(default=list, encoder=JSON_DUMPS, decoder=JSON_LOADS)),
                ('status', fields.CharField(default='active', db_index=True, max_length=20)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
                ('updated_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
                ('last_used_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'api_clients', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='AccessToken',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('token_hash', fields.CharField(unique=True, db_index=True, max_length=64)),
                ('client', fields.ForeignKeyField('models.ApiClient', source_field='client_id', db_constraint=True, to_field='id', related_name='access_tokens', on_delete=OnDelete.CASCADE)),
                ('scopes', fields.JSONField(default=list, encoder=JSON_DUMPS, decoder=JSON_LOADS)),
                ('expires_at', fields.DatetimeField(db_index=True, auto_now=False, auto_now_add=False)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'access_tokens', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='ApiClientRedirectUri',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('client', fields.ForeignKeyField('models.ApiClient', source_field='client_id', db_constraint=True, to_field='id', related_name='redirect_uris', on_delete=OnDelete.CASCADE)),
                ('redirect_uri', fields.CharField(db_index=True, max_length=500)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'api_client_redirect_uris', 'app': 'models', 'unique_together': (('client', 'redirect_uri'),), 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='AuthorizationCode',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('code_hash', fields.CharField(unique=True, db_index=True, max_length=64)),
                ('client', fields.ForeignKeyField('models.ApiClient', source_field='client_id', db_constraint=True, to_field='id', related_name='authorization_codes', on_delete=OnDelete.CASCADE)),
                ('redirect_uri', fields.CharField(max_length=500)),
                ('scopes', fields.JSONField(default=list, encoder=JSON_DUMPS, decoder=JSON_LOADS)),
                ('code_challenge', fields.CharField(max_length=128)),
                ('code_challenge_method', fields.CharField(max_length=10)),
                ('expires_at', fields.DatetimeField(db_index=True, auto_now=False, auto_now_add=False)),
                ('consumed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'authorization_codes', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='RefreshToken',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('token_hash', fields.CharField(unique=True, db_index=True, max_length=64)),
                ('family_id', fields.CharField(db_index=True, max_length=64)),
                ('client', fields.ForeignKeyField('models.ApiClient', source_field='client_id', db_constraint=True, to_field='id', related_name='refresh_tokens', on_delete=OnDelete.CASCADE)),
                ('scopes', fields.JSONField(default=list, encoder=JSON_DUMPS, decoder=JSON_LOADS)),
                ('expires_at', fields.DatetimeField(db_index=True, auto_now=False, auto_now_add=False)),
                ('consumed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'refresh_tokens', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='WeatherLookup',
            fields=[
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('city', fields.CharField(max_length=120)),
                ('country_code', fields.CharField(max_length=2)),
                ('location_name', fields.CharField(max_length=120)),
                ('location_country', fields.CharField(null=True, max_length=120)),
                ('location_timezone', fields.CharField(null=True, max_length=120)),
                ('latitude', fields.FloatField()),
                ('longitude', fields.FloatField()),
                ('weather', fields.JSONField(encoder=JSON_DUMPS, decoder=JSON_LOADS)),
                ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ],
            options={'table': 'weather_lookups', 'app': 'models', 'pk_attr': 'id'},
            bases=['Model'],
        ),
    ]
