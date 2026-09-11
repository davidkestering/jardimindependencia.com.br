"""Residentes, transferência de acesso, escolha/troca de apto. Regra: só 1 CPF entra por apto. Limpa o que cria:
docker exec condominio-app python scripts/check_residentes.py"""
import sys, time
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Morador, Residente, Unidade
from termo import TERMO

A, R, S = "52998224725", "11144477735", "16899535009"
NASC = "1980-05-10"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Residente).where(Residente.cpf.in_([A, R, S])))
        db.execute(delete(Morador).where(Morador.cpf.in_([A, R, S]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


def login(cpf):
    lc = TestClient(app, base_url="https://t"); cp = auth.captcha_novo()
    r = lc.post("/morador/login", data={"cpf": cpf, "nascimento": NASC, "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}, follow_redirects=False)
    return lc, r


def unidade(db, b, a):
    return db.scalar(select(Unidade).where(Unidade.bloco == b, Unidade.apto == a))


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        u1 = unidade(db, "01", "101")
        db.add(Morador(unidade_id=u1.id, nome="Ana Titular", cpf=A, nascimento=auth.parse_data(NASC), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO)); db.commit()
        ma = db.scalar(select(Morador).where(Morador.cpf == A))
    ac, mc = cliente("admin", adm.id), cliente("morador", ma.id)
    base = dict(nascimento=NASC, email="r@example.com", telefone="(91) 98888-0000", declaracao="sim")
    assert "aceitar a declaração" in mc.post("/morador/residentes", data={**base, "nome": "Sem aceite", "cpf": R, "tipo": "morador", "declaracao": ""}).text
    assert "art. 299" in mc.get("/morador/residentes").text

    # residente NÃO loga, antes e depois de cadastrado
    assert "não conferem" in login(R)[1].text
    assert "titular" in mc.post("/morador/residentes", data={**base, "nome": "X", "cpf": A, "tipo": "morador"}).text
    assert mc.post("/morador/residentes", data={**base, "nome": "Rui Inquilino", "cpf": R, "tipo": "inquilino"}, follow_redirects=False).status_code == 303
    assert mc.post("/morador/residentes", data={**base, "nome": "Sol Moradora", "cpf": S, "tipo": "morador"}, follow_redirects=False).status_code == 303
    assert "em nome de Rui Inquilino" in mc.post("/morador/residentes", data={**base, "nome": "Dup", "cpf": R, "tipo": "morador"}).text
    time.sleep(0.3); assert sum(1 for p, a, _ in enviados if p == mail.MAIL_CONTATO and "Residente cadastrado" in a) == 2, [a for _, a, _ in enviados]
    pg = mc.get("/morador/residentes").text; assert "Rui Inquilino" in pg and "Inquilino" in pg and "Sol Moradora" in pg
    with SessionLocal() as db:
        rr = db.scalar(select(Residente).where(Residente.cpf == R)); assert rr.termo_texto == TERMO and rr.termo_aceito_em and rr.termo_ip
    assert "declaração aceita em" in ac.get(f"/admin/moradores/{ma.id}").text
    assert "não conferem" in login(R)[1].text
    assert "2 residente(s)" in mc.get("/morador").text and "administrando <strong>Bloco 01 · Apto 101" in mc.get("/morador").text
    with SessionLocal() as db:
        rs = {r.cpf: r.id for r in db.scalars(select(Residente).where(Residente.unidade_id == u1.id))}
    mc.post(f"/morador/residentes/{rs[S]}/excluir")
    assert "Sol Moradora" not in mc.get("/morador/residentes").text
    time.sleep(0.3); assert any(p == mail.MAIL_CONTATO and "Residente removido: Sol Moradora" in a for p, a, _ in enviados)
    with SessionLocal() as db:
        sr = db.get(Residente, rs[S]); assert sr and sr.excluido_em and "Ana Titular" in sr.excluido_por and sr.excluido_ip  # lógico
    assert "removido por condômino Ana Titular" in ac.get(f"/admin/moradores/{ma.id}").text
    # recadastrar o mesmo CPF reativa a linha (unique unidade+cpf)
    assert mc.post("/morador/residentes", data={**base, "nome": "Sol Moradora", "cpf": S, "tipo": "inquilino"}, follow_redirects=False).status_code == 303
    assert "Sol Moradora" in mc.get("/morador/residentes").text
    mc.post(f"/morador/residentes/{rs[S]}/excluir")

    # admin: lista unificada e filtros
    lst = ac.get("/admin/moradores?bloco=01&apto=101").text
    assert "Cadastrado por <strong>Ana Titular</strong>" in lst and "· IP " in lst
    assert "Ana Titular" in lst and "Rui Inquilino" in lst and "Residente · inquilino" in lst and "Cadastrado pelo condômino Ana Titular" in lst and "Solicitou no site" in lst
    so_res = ac.get("/admin/moradores?status=residente&bloco=01").text; assert "Rui Inquilino" in so_res and "Titular do acesso" not in so_res
    assert "Rui Inquilino" not in ac.get("/admin/moradores?bloco=02").text
    assert "Rui Inquilino" in ac.get(f"/admin/moradores/{ma.id}").text

    # transferência: A -> transferido (com CPF/data), R -> PENDENTE; admin aprova; só R loga
    r = mc.post(f"/morador/residentes/{rs[R]}/transferir"); assert r.status_code == 200 and "Acesso transferido" in r.text and "pendente de aprovação" in r.text
    assert mc.get("/morador", follow_redirects=False).status_code == 303  # sessão de A caiu
    with SessionLocal() as db:
        ma2 = db.get(Morador, ma.id); mr = db.scalar(select(Morador).where(Morador.cpf == R, Morador.unidade_id == u1.id))
        assert ma2.status == "transferido" and "111.444.777-35" in ma2.decidido_por and ma2.decidido_em and ma2.decidido_ip
        assert mr.status == "pendente" and mr.origem == "transferencia"
        rr = db.scalar(select(Residente).where(Residente.cpf == R)); assert rr and rr.excluido_em and "virou titular" in rr.excluido_por
        ares = db.scalar(select(Residente).where(Residente.cpf == A, Residente.unidade_id == u1.id)); assert ares and ares.tipo == "morador"
        assert db.scalar(select(Morador).where(Morador.unidade_id == u1.id, Morador.status == "aprovado")) is None  # ninguém aprovado até o admin decidir
    assert "transferiu o acesso" in login(A)[1].text
    assert "aguarda aprovação" in login(R)[1].text
    time.sleep(0.3); assuntos = {p: a for p, a, _ in enviados}
    assert "transferiu o acesso" in assuntos["ana@example.com"] and "aguarde a liberação" in assuntos["r@example.com"] and all(t in [c for p, _, c in enviados if p == "r@example.com"][0] for t in ("Aguarde o e-mail de liberação", "login será sempre o seu CPF", "111.444.777-35")) and any("[Site] Acesso transferido" in a for _, a, _ in enviados)
    corpo_a = [c for p, _, c in enviados if p == "ana@example.com"][0]; assert "111.444.777-35" in corpo_a and "pendente de aprovação" in corpo_a
    assert "transferiu o acesso" in ac.get("/admin/moradores?status=transferido").text
    assert ac.post(f"/admin/moradores/{mr.id}/status", data={"status": "aprovado"}, follow_redirects=False).status_code == 303
    lc, r = login(R); assert r.status_code == 303
    assert lc.get("/morador", follow_redirects=False).headers["location"] == "/morador/termo"  # nunca aceitou a declaração
    assert lc.post("/morador/termo", data={"declaracao": "sim"}, follow_redirects=False).status_code == 303
    assert "administrando <strong>Bloco 01 · Apto 101" in lc.get("/morador").text
    with SessionLocal() as db:
        assert db.scalar(select(Morador).where(Morador.unidade_id == u1.id, Morador.status == "aprovado")).cpf == R  # 1 acesso por apto
    det = ac.get(f"/admin/moradores/{mr.id}").text; assert "Acesso transferido pelo condômino" in det and "Ana Titular" in det
    det_a = ac.get(f"/admin/moradores/{ma.id}").text; assert "transferiu o acesso" in det_a and "111.444.777-35" in det_a

    # R também titular em 02/102: escolha no login e troca com confirmação
    with SessionLocal() as db:
        u2 = unidade(db, "02", "102")
        db.add(Morador(unidade_id=u2.id, nome="Rui Inquilino", cpf=R, nascimento=auth.parse_data(NASC), email="r@example.com", telefone="91988880000", status="aprovado", termo_texto=TERMO)); db.commit()
        mr2 = db.scalar(select(Morador).where(Morador.cpf == R, Morador.unidade_id == u2.id))
    lc, r = login(R); assert r.status_code == 200 and "Qual apartamento" in r.text and "Bloco 02 · Apto 102" in r.text
    token = r.text.split('name="token" value="')[1].split('"')[0]
    assert lc.post("/morador/escolher", data={"token": token, "mid": str(ma.id)}).status_code == 403  # apto de outro CPF/negado
    assert lc.post("/morador/escolher", data={"token": token, "mid": str(mr2.id)}, follow_redirects=False).status_code == 303
    pg = lc.get("/morador").text; assert "administrando <strong>Bloco 02 · Apto 102" in pg and f'value="{mr.id}"' in pg and "Trocar de apto" in pg
    pg = lc.get(f"/morador/trocar/{mr.id}").text; assert "sair do <strong>Bloco 02 · Apto 102</strong>" in pg and "administrar o <strong>Bloco 01 · Apto 101</strong>" in pg
    assert lc.post(f"/morador/trocar/{ma.id}").status_code == 403
    assert lc.post(f"/morador/trocar/{mr.id}", follow_redirects=False).status_code == 303
    assert "administrando <strong>Bloco 01 · Apto 101" in lc.get("/morador").text
    print("check_residentes ok")
finally:
    limpar()
