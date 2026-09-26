"""Política de privacidade e exclusão da própria conta pelo condômino (App Store 5.1.1(v)). Limpa o que cria:
docker exec condominio-app python scripts/check_conta.py"""
import sys
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True
mail._gravar_historico = lambda *a, **k: None

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Morador, Unidade
from termo import TERMO

A, NASC = "52998224725", "1980-05-10"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Morador).where(Morador.cpf == A)); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


def login():
    lc = TestClient(app, base_url="https://t"); cp = auth.captcha_novo()
    return lc.post("/morador/login", data={"cpf": A, "nascimento": NASC, "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}, follow_redirects=False)


def unidade(db, b, a):
    return db.scalar(select(Unidade).where(Unidade.bloco == b, Unidade.apto == a))


limpar()
try:
    c = TestClient(app, base_url="https://t")
    # política pública, com as três URLs que a Apple pode conferir
    pg = c.get("/privacidade").text
    for trecho in ("CPF", "bloco/apartamento", "Ocorrências", "Excluir minha conta", "contato@jardimindependencia.com.br", "LGPD"):
        assert trecho in pg, trecho
    for alias in ("/politica-de-privacidade", "/privacy"):
        r = c.get(alias, follow_redirects=False); assert r.status_code == 301 and r.headers["location"] == "/privacidade", alias
    assert 'href="/privacidade"' in c.get("/").text  # rodapé

    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        base = dict(nome="Ana Conta", cpf=A, nascimento=auth.parse_data(NASC), email="ana@example.com", telefone="91999990000", termo_texto=TERMO)
        db.add(Morador(unidade_id=unidade(db, "01", "101").id, status="aprovado", **base))
        db.add(Morador(unidade_id=unidade(db, "02", "102").id, status="pendente", **base)); db.commit()
        ma = db.scalar(select(Morador).where(Morador.cpf == A, Morador.status == "aprovado"))
    mc, ac = cliente("morador", ma.id), cliente("admin", adm.id)
    assert "Excluir minha conta" in mc.get("/morador").text
    pg = mc.get("/morador/excluir-conta").text
    assert "Bloco 01 · Apto 101" in pg and "Bloco 02 · Apto 102" in pg and "todos os apartamentos" in pg
    assert "marcar a caixa" in mc.post("/morador/excluir-conta", data={}).text
    with SessionLocal() as db:
        assert db.get(Morador, ma.id).status == "aprovado"  # sem confirmação, nada muda
    r = mc.post("/morador/excluir-conta", data={"confirmo": "sim"})
    assert r.status_code == 200 and "Conta excluída" in r.text and "Bloco 01 · Apto 101, Bloco 02 · Apto 102" in r.text
    assert mc.get("/morador", follow_redirects=False).status_code == 303  # sessão derrubada
    with SessionLocal() as db:
        todos = db.scalars(select(Morador).where(Morador.cpf == A)).all()
        assert len(todos) == 2 and all(m.status == "excluido" and m.decidido_por == "o próprio condômino" and m.decidido_em and m.decidido_ip for m in todos)
    assert [e[0] for e in enviados] == ["ana@example.com", mail.MAIL_CONTATO], enviados
    assert "/privacidade" in enviados[0][2] and "Bloco 01 · Apto 101, Bloco 02 · Apto 102" in enviados[1][2]
    # login depois: mensagem própria; apto livre para novo cadastro; admin vê como histórico
    assert "excluída a seu pedido" in login().text
    from routers.morador import ocupante
    with SessionLocal() as db:
        assert ocupante(db, unidade(db, "01", "101").id) is None
    assert "excluiu a conta" in ac.get(f"/admin/moradores/{ma.id}").text and "Conta excluída pelo próprio condômino" in ac.get(f"/admin/moradores/{ma.id}").text
    assert f"/admin/moradores/{ma.id}" in ac.get("/admin/moradores?status=excluido").text
    assert ac.post(f"/admin/moradores/{ma.id}/status", data={"status": "aprovado"}).status_code == 400  # final
    print("check_conta ok")
finally:
    limpar()
