import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from agripulse_api import models  # noqa: F401  (registers tables)
from agripulse_api.config import get_settings
from agripulse_api.db import Base

config = context.config
# alembic.ini holds a placeholder; a caller (e.g. tests) may pass a real URL via Config.set_main_option.
if config.get_main_option("sqlalchemy.url") in (None, "", "set-from-env"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        # DB_SCHEMA (persistent public demo): keep Alembic's version table in that schema too, or it would find another
        # schema's alembic_version through the search_path and migrate the wrong tables.
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite",
                          version_table_schema=os.environ.get("DB_SCHEMA") or None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
