"""Alembic environment — wired to the app's settings and metadata.

The URL comes from app.core.config.settings (backend/.env), matching
app.core.db.engine exactly. ALEMBIC_DATABASE_URL overrides for
one-off runs against sandboxes.

Autogenerate imports every model module so Base.metadata is complete
before diffing — the same list app.main::_init_database() maintains.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.models.agent import Base

# Import all model modules so Base.metadata knows every table.
# Keep in sync with app/main.py::_init_database().
import app.models.task  # noqa: F401
import app.models.user  # noqa: F401
import app.models.approval  # noqa: F401
import app.models.action_ledger  # noqa: F401
import app.models.agent_run  # noqa: F401
import app.models.policy  # noqa: F401
import app.models.lane_config  # noqa: F401
import app.models.tally_staging  # noqa: F401
import app.models.sync_watermark  # noqa: F401
import app.models.gateway_event  # noqa: F401
import app.models.vendor  # noqa: F401
import app.models.purchase_request  # noqa: F401
import app.models.purchase_order  # noqa: F401
import app.models.goods_receipt  # noqa: F401
import app.models.invoice  # noqa: F401
import app.models.rfq  # noqa: F401
import app.models.workflow  # noqa: F401
import app.models.enterprise  # noqa: F401
import app.models.scheme  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Credentials stay out of alembic.ini — env or .env supply them.
import os  # noqa: E402

database_url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Skip the cloud-side mirror decision for now: every table lives in
    the one database until the Local/Cloud split migration lands."""
    return True


def run_migrations_offline() -> None:
    """Generate SQL without a live DB (alembic upgrade --sql)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
