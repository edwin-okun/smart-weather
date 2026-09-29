from app.config import settings

# The app tests run against throwaway in-memory SQLite databases, so let startup
# create the tables straight from the models. tests/test_migrations.py checks
# that the migrations produce the same schema.
settings.generate_db_schemas = True
settings.run_db_migrations_on_startup = False
