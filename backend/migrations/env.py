from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import settings

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)


def run_migrations_online() -> None:
    url = context.config.attributes.get("database_url", settings.database_url)
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
