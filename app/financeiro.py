"""Regra de inadimplência compartilhada (admin e votação): unidade é inadimplente enquanto houver registro ativo."""
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from models import Inadimplencia


def unidade_inadimplente(db: Session, unidade_id) -> bool:
    return db.scalar(select(exists().where(Inadimplencia.unidade_id == unidade_id, Inadimplencia.encerrado_em.is_(None))))
