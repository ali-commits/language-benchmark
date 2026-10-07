import os

from alembic import context
from sqlalchemy import create_engine

engine = create_engine(
    os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
