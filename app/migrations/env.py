from alembic import context
from db import Base, engine
import models  # noqa: F401  (registra as tabelas)

target_metadata = Base.metadata


def run():
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run()
