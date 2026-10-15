import os
from alembic import context
from sqlalchemy import create_engine, text
from shared.models import Base
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql.base import ischema_names

ischema_names['vector'] = Vector


def migrate(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get('connection')
if connection is not None:
    migrate(connection)
else:
    engine = create_engine(os.environ['DATABASE_URL'])
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(71402002)'))
        migrate(connection)
