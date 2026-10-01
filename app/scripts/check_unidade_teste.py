"""Unidades de teste (Bloco 99, aptos 999 a 996): para o site não existem. Confere que ficam fora de listas, seleções, contagens
e buscas por bloco e apto, e que só o processo dos testes (models.EM_TESTE) as enxerga.
Não cria nada: docker exec condominio-app python scripts/check_unidade_teste.py"""
import re
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from fastapi.testclient import TestClient
from sqlalchemy import func, select
import auth
import models
from db import SessionLocal
from garagens import ULTIMA
from main import app
from models import APTOS_TESTE, BLOCO_TESTE, AdminUser, Morador, Unidade
from routers.financeiro import mapa_unidades
from routers.garagem import catalogo

REAIS = 396  # apartamentos do condomínio
OPCAO = re.compile(rf'<option[^>]*>\s*{BLOCO_TESTE}\s*</option>|value="{BLOCO_TESTE}"|Bloco {BLOCO_TESTE}|BL {BLOCO_TESTE} ')


def captcha():
    cp = auth.captcha_novo()
    return {"captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"]), "declaracao": "sim"}


def contagem(db):
    return db.scalar(select(func.count()).select_from(Unidade).where(Unidade.em_uso, Unidade.apto != ""))


assert not models.EM_TESTE  # este teste começa como o servidor: sem a visão das unidades de teste
c = TestClient(app, base_url="https://t")
with SessionLocal() as db:
    us = db.scalars(select(Unidade).where(Unidade.bloco == BLOCO_TESTE)).all()
    assert sorted(u.apto for u in us) == sorted(APTOS_TESTE) and not any(u.ativa for u in us)
    assert all(u.teste and u.garagem > ULTIMA and u.garagem == u.garagem_convencao for u in us)  # garagem fora da numeração real
    assert not db.scalar(select(Morador.id).where(Morador.unidade_id.in_([u.id for u in us])))  # nada sobra nelas entre um teste e outro
    adm = db.scalar(select(AdminUser).where(AdminUser.master))
    ac = TestClient(app, base_url="https://t"); ac.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(adm.id)))

    # como o site vê: fora das listas de escolha, do catálogo de garagens e da contagem de apartamentos
    mapa = mapa_unidades(db)
    assert BLOCO_TESTE not in mapa and sum(len(v) for v in mapa.values()) == REAIS == contagem(db)
    assert not any(u.teste for u in catalogo(db)) and not any(u.em_uso or u.visivel for u in us)

for url in ("/contato", "/morador/cadastro"):  # páginas públicas
    assert not OPCAO.search(c.get(url).text), url
for url in ("/admin", "/admin/moradores", "/admin/financeiro", "/admin/garagem?mostrar=todos", "/admin/animais?situacao=pendente", "/admin/interfone"):
    r = ac.get(url); assert r.status_code == 200 and not OPCAO.search(r.text), url

# quem informa o bloco e o apto dela não a alcança: cadastro, contato e inadimplência
cad = dict(nome="Fulano", cpf="52998224725", nascimento="1980-05-10", email="f@example.com", telefone="(91) 99999-0000", bloco=BLOCO_TESTE, apto="999")
assert f"Unidade bloco {BLOCO_TESTE} apto 999 não encontrada" in c.post("/morador/cadastro", data={**cad, **captcha()}).text
assert "não conferem" in c.post("/contato", data={"nome": "Zé", "email": "ze@example.com", "mensagem": "oi", "bloco": BLOCO_TESTE, "apto": "999", **captcha()}).text
assert "não encontrada" in ac.post("/admin/financeiro", data={"bloco": BLOCO_TESTE, "apto": "999", "observacao": "x"}).text
assert ac.post("/admin/moradores", data={k: v for k, v in cad.items()}).status_code == 400  # nem a administração cadastra alguém nela

# dentro do processo dos testes, as mesmas consultas passam a enxergá-las
models.EM_TESTE = True
with SessionLocal() as db:
    assert mapa_unidades(db)[BLOCO_TESTE] == sorted(APTOS_TESTE) and contagem(db) == REAIS + len(APTOS_TESTE)
    assert sum(u.teste for u in catalogo(db)) == len(APTOS_TESTE)
assert OPCAO.search(c.get("/contato").text) and OPCAO.search(ac.get("/admin/garagem?mostrar=todos").text)
print("check_unidade_teste ok")
