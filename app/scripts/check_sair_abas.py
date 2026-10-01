"""Checagem do "sair" com várias abas abertas (no app, cada aba é uma WebView): ao sair, o servidor fecha com 4401 as conexões
do interfone daquela sessão, e o interfone.js recarrega a página. Fala com o servidor de verdade (localhost:8000) e limpa o que cria:
docker exec condominio-app python scripts/check_sair_abas.py"""
import asyncio
import sys
sys.path.insert(0, "/app")

import httpx
from sqlalchemy import delete, select
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import auth
from db import SessionLocal
from models import Morador
from termo import TERMO
from unidades_teste import unidades

CPF = "52998224725"
WS, HTTP = "ws://localhost:8000/ws/interfone", "http://localhost:8000"
UA_APP = "Mozilla/5.0 (Linux; Android 14) JardimIndependenciaApp/1.0"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()


def aba(cookie=None):
    return connect(WS, additional_headers={"Cookie": f"{auth.COOKIE}={cookie}"} if cookie else None)


async def fechou_com(ws, espera=4):
    """Código com que o servidor fechou a conexão, ou None se ela continua aberta."""
    try:
        await asyncio.wait_for(ws.recv(), espera)
    except ConnectionClosed:
        return ws.close_code
    except asyncio.TimeoutError:
        return None


async def main(mid):
    aparelho1, aparelho2 = auth.criar_sessao("morador", str(mid)), auth.criar_sessao("morador", str(mid))
    assert aparelho1 != aparelho2
    async with aba(aparelho1) as aba1, aba(aparelho1) as aba2, aba(aparelho2) as outro:
        assert await fechou_com(outro, 0.5) is None  # conectadas e ociosas
        async with httpx.AsyncClient() as cli:
            r = await cli.get(f"{HTTP}/morador/sair", headers={"Cookie": f"{auth.COOKIE}={aparelho1}", "User-Agent": UA_APP})
        assert r.status_code == 303 and r.headers["location"] == "/morador/login" and 'sessao=""' in r.headers["set-cookie"]
        assert await fechou_com(aba1) == 4401 and await fechou_com(aba2) == 4401  # as outras abas do aparelho que saiu
        assert await fechou_com(outro, 1) is None  # outro aparelho da mesma unidade continua conectado
    async with aba() as sem:  # aba antiga que tenta reconectar sem cookie: aceita e fecha com 4401, para o interfone.js recarregar
        assert await fechou_com(sem) == 4401


limpar()
try:
    with SessionLocal() as db:
        u, = unidades(db)
        m = Morador(unidade_id=u.id, nome="Ana Teste", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com",
                    telefone="91999990000", status="aprovado", termo_texto=TERMO)
        db.add(m); db.commit()
        mid = m.id
    asyncio.run(main(mid))
    print("check_sair_abas ok")
finally:
    limpar()
