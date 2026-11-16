from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DATABASE_URL

# Sessão no fuso do condomínio: toda data/hora lida do banco já vem em horário local (os templates usam strftime direto).
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"options": "-c timezone=America/Belem"})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
