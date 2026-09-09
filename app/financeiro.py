"""Regras financeiras compartilhadas (admin, morador e votação)."""
from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from models import Cobranca


def em_atraso(c: Cobranca, hoje: date | None = None) -> bool:
    return c.status == "aberta" and c.vencimento < (hoje or date.today())


def unidade_inadimplente(db: Session, unidade_id, hoje: date | None = None) -> bool:
    hoje = hoje or date.today()
    return db.scalar(select(exists().where(Cobranca.unidade_id == unidade_id, Cobranca.status == "aberta",
                                           Cobranca.vencimento < hoje)))


def gerar_cobranca(c: Cobranca) -> None:
    """ponytail: gateway manual. Quando o provedor (Asaas/Efí/Mercado Pago) for escolhido, esta função
    chama a API e preenche c.pix_copia_cola, c.boleto_url e c.gateway_ref; o webhook marca c.status='paga'."""
    return None


if __name__ == "__main__":
    from datetime import timedelta
    hoje = date(2026, 9, 9)
    assert em_atraso(Cobranca(status="aberta", vencimento=hoje - timedelta(days=1)), hoje)
    assert not em_atraso(Cobranca(status="aberta", vencimento=hoje), hoje)
    assert not em_atraso(Cobranca(status="paga", vencimento=hoje - timedelta(days=30)), hoje)
    print("financeiro ok")
