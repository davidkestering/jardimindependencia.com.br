"""Unidades dos testes automatizados: Bloco 99, aptos 999 (a principal), 998, 997 e 996. Para o site elas não existem
(models.BLOCO_TESTE): importar este módulo liga a visão delas só neste processo, o do teste. Todo check_*.py usa
`unidades(db, n)` em vez de apartamentos reais e apaga ao final o que criou nelas."""
import sys
sys.path.insert(0, "/app")
from sqlalchemy import select

import models

models.EM_TESTE = True


def unidades(db, n: int = 1) -> list[models.Unidade]:
    """As n primeiras unidades de teste, a partir do Apto 999."""
    us = db.scalars(select(models.Unidade).where(models.Unidade.bloco == models.BLOCO_TESTE).order_by(models.Unidade.apto.desc()).limit(n)).all()
    assert len(us) == n, "unidades de teste ausentes: reinicie o app (o seed as cria)"
    return us
