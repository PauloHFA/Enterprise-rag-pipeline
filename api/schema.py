"""Versioned migrations, serialized across replicas."""
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from . import database


def initialize_schema():
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).resolve().parents[1] / 'migrations'))
    with database.engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(71402002)'))
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
