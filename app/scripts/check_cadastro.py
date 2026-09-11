"""Checagem do fluxo de cadastro/aprovação. Roda dentro do container contra o banco real e limpa o que criou:
docker exec condominio-app python scripts/check_cadastro.py"""
import sys, time
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: enviados.append((para, assunto, corpo)) or True  # sem e-mail real

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Inadimplencia, Morador, Unidade

CPF1, CPF2, CPF3 = "52998224725", "11144477735", "16899535009"
BASE = dict(nascimento="1980-05-10", email="teste@example.com", telefone="(91) 99999-0000", declaracao="sim")
c = TestClient(app, base_url="https://t")


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Inadimplencia).where(Inadimplencia.observacao.in_(["taxa 08/2026", "de novo"])))
        db.execute(delete(Morador).where(Morador.cpf.in_([CPF1, CPF2, CPF3]))); db.commit()


def captcha(certo=True):
    cp = auth.captcha_novo(); r = auth._captcha.loads(cp["token"])["r"]
    return {"captcha_token": cp["token"], "captcha": str(r if certo else r + 1)}


def cadastrar(nome, cpf, bloco, apto, **extra):
    return c.post("/morador/cadastro", data={**BASE, **captcha(), "nome": nome, "cpf": cpf, "bloco": bloco, "apto": apto, **extra}).text


def morador(cpf, bloco):
    with SessionLocal() as db:
        return db.scalar(select(Morador).join(Unidade).where(Morador.cpf == cpf, Unidade.bloco == bloco))


def admin_client():
    with SessionLocal() as db:
        a = db.scalar(select(AdminUser))
    ac = TestClient(app, base_url="https://t"); ac.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(a.id)))
    return ac


def login(cpf):
    lc = TestClient(app, base_url="https://t")
    r = lc.post("/morador/login", data={"cpf": cpf, "nascimento": BASE["nascimento"], **captcha()}, follow_redirects=False)
    return lc, r


limpar()
try:
    # captcha nos logins
    assert "verificação incorreta" in c.post("/morador/login", data={"cpf": CPF1, "nascimento": "1980-05-10", **captcha(False)}).text
    assert "verificação incorreta" in c.post("/admin/login", data={"login": "x", "senha": "y", **captcha(False)}).text
    assert "Login ou senha incorretos" in c.post("/admin/login", data={"login": "x", "senha": "y", **captcha()}).text
    # obrigatórios
    assert c.post("/morador/cadastro", data={"nome": "X", "cpf": CPF1, "nascimento": "1980-05-10", "bloco": "01", "apto": "101"}).status_code == 422
    assert "aceitar a declaração" in cadastrar("Sem aceite", CPF1, "01", "101", declaracao="")
    assert "verificação incorreta" in cadastrar("Captcha ruim", CPF1, "01", "101", **captcha(certo=False))
    # contato: captcha, selects e unidade do condômino logado
    ct = dict(nome="Zé", email="ze@example.com", mensagem="oi")
    assert "verificação incorreta" in c.post("/contato", data={**ct, **captcha(False)}).text
    assert "não conferem" in c.post("/contato", data={**ct, **captcha(), "bloco": "01", "apto": "201"}).text
    assert "Mensagem enviada" in c.post("/contato", data={**ct, **captcha(), "bloco": "01", "apto": "101"}).text
    assert 'name="bloco"' in c.get("/contato").text and 'value="27"' in c.get("/contato").text
    assert "Telefone inválido" in cadastrar("Tel ruim", CPF1, "01", "101", telefone="123")
    # A ok, B mesmo apto bloqueado, C mesmo CPF outro apto ok
    assert "Solicitação enviada" in cadastrar("Ana Teste", CPF1, "01", "101")
    assert "já foi registrado em nome de Ana Teste (aguardando aprovação)" in cadastrar("Bia Teste", CPF2, "01", "101")
    assert "Solicitação enviada" in cadastrar("Ana Teste", CPF1, "02", "102")
    a, cc = morador(CPF1, "01"), morador(CPF1, "02")
    # login pendente
    _, r = login(CPF1); assert "aguarda aprovação" in r.text
    # admin: lista, detalhe, autorizar A, negar C
    ac = admin_client()
    lst = ac.get("/admin/moradores?status=pendente").text; assert f"/admin/moradores/{a.id}" in lst and f"/admin/moradores/{cc.id}" in lst
    det = ac.get(f"/admin/moradores/{a.id}").text; assert "Autorizar acesso" in det and "Negar acesso" in det and "Ana Teste" in det
    assert ac.post(f"/admin/moradores/{a.id}/status", data={"status": "aprovado"}, follow_redirects=False).status_code == 303
    assert ac.post(f"/admin/moradores/{cc.id}/status", data={"status": "negado"}, follow_redirects=False).status_code == 303
    assert ac.post(f"/admin/moradores/{cc.id}/status", data={"status": "aprovado"}).status_code == 400  # negado é final
    a, cc = morador(CPF1, "01"), morador(CPF1, "02")
    assert a.status == "aprovado" and a.decidido_por and a.decidido_ip and cc.status == "negado"
    assert "Decidido por <strong>" in ac.get("/admin/moradores?status=aprovado").text and "· IP " in ac.get("/admin/moradores?status=aprovado").text
    assert "negado" in ac.get("/admin/moradores?status=negado").text
    assert "Habilitar novo registro" in ac.get(f"/admin/moradores/{a.id}").text
    # apto negado liberado
    assert "Solicitação enviada" in cadastrar("Duda Teste", CPF3, "02", "102")
    # login aprovado + trocar unidade
    lc, r = login(CPF1); assert r.status_code == 303 and lc.get("/morador").status_code == 200  # só 01/101 aprovado
    pg = lc.get("/contato").text; assert 'value="01" selected' in pg and 'value="101" selected' in pg and 'value="27"' not in pg and "Ana Teste" in pg
    assert "Mensagem enviada" in lc.post("/contato", data={**ct, **captcha(), "bloco": "01", "apto": "101"}).text
    assert "Condômino logado: Ana Teste" in enviados[-1][2]
    d = morador(CPF3, "02"); assert lc.get(f"/morador/trocar/{d.id}", follow_redirects=False).status_code == 403
    # inadimplência: registro manual com observação obrigatória, único por unidade, reflete na votação
    from financeiro import unidade_inadimplente
    assert "não encontrada" in ac.post("/admin/financeiro", data={"bloco": "01", "apto": "201", "observacao": "x"}).text
    assert "obrigatória" in ac.post("/admin/financeiro", data={"bloco": "01", "apto": "101", "observacao": "  "}).text
    assert "Bloco 01 · Apto 101" in ac.post("/admin/financeiro", data={"bloco": "01", "apto": "101", "observacao": "taxa 08/2026"}).text
    assert "já está registrada" in ac.post("/admin/financeiro", data={"bloco": "01", "apto": "101", "observacao": "de novo"}).text
    with SessionLocal() as db:
        assert unidade_inadimplente(db, a.unidade_id)
        iid = db.scalar(select(Inadimplencia.id).where(Inadimplencia.unidade_id == a.unidade_id, Inadimplencia.encerrado_em.is_(None)))
    assert ac.post(f"/admin/financeiro/{iid}/encerrar", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert not unidade_inadimplente(db, a.unidade_id)
    assert "Histórico" in ac.get("/admin/financeiro").text
    # habilitar novo registro (revogar) libera o apto e derruba o login
    assert ac.post(f"/admin/moradores/{a.id}/status", data={"status": "bloqueado"}).status_code == 400  # status extinto
    assert ac.post(f"/admin/moradores/{a.id}/status", data={"status": "negado"}, follow_redirects=False).status_code == 303
    _, r = login(CPF1); assert "não autorizado" in r.text
    assert lc.get("/morador", follow_redirects=False).status_code == 303  # sessão antiga cai
    assert "Solicitação enviada" in cadastrar("Bia Teste", CPF2, "01", "101")
    time.sleep(0.3)
    assuntos = " | ".join(a for _, a, _ in enviados)
    assert "Acesso liberado" in assuntos and "não aprovada" in assuntos and "Acesso encerrado" in assuntos, assuntos
    assert "pendente" in ac.get("/admin").text  # menu único com contador de pendentes
    print("check_cadastro ok")
finally:
    limpar()
