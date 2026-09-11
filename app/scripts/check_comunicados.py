"""Checagem dos comunicados (rascunho/condôminos/público, notificação, badge). Roda no container e limpa o que cria:
docker exec condominio-app python scripts/check_comunicados.py"""
import sys, time
sys.path.insert(0, "/app")
import mail, interfone
enviados, pushes = [], []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True  # ignora e-mails de log
interfone.push_para_todos = lambda payload, ttl=0: pushes.append(payload)

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Comunicado, Morador, Unidade
from termo import TERMO

CPF = "52998224725"
TIT = "Comunicado de teste automático"
TEXTO = ("Manutenção da caixa de água. " * 20).strip()  # > 200 chars


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Comunicado).where(Comunicado.titulo.like(TIT + "%")))
        db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser))
        u1, u2 = [db.scalar(select(Unidade).where(Unidade.bloco == b, Unidade.apto == a)) for b, a in (("01", "101"), ("02", "102"))]
        for u in (u1, u2):  # mesmo CPF/e-mail em 2 aptos: deve receber 1 e-mail
            db.add(Morador(unidade_id=u.id, nome="Ana Teste", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.commit()
        m1 = db.scalar(select(Morador).where(Morador.cpf == CPF, Morador.unidade_id == u1.id))
    ac, mc, pub = cliente("admin", adm.id), cliente("morador", m1.id), TestClient(app, base_url="https://t")

    # rascunho: só admin vê
    assert ac.post("/admin/comunicados", data={"titulo": TIT, "texto": TEXTO, "visibilidade": "rascunho"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        c = db.scalar(select(Comunicado).where(Comunicado.titulo == TIT)); cid = c.id; assert c.publicado_em is None
        assert c.autor == adm.login and c.criado_ip
    assert TIT in ac.get("/admin/comunicados").text and f"/admin/comunicados/{cid}/preview" in ac.get("/admin/comunicados").text
    pv = ac.get(f"/admin/comunicados/{cid}/preview").text
    assert TEXTO in pv and "Pré-visualização" in pv and "Publicar: só condôminos" in pv and "Publicar: público" in pv and "Voltar a rascunho" not in pv
    assert TIT not in pub.get("/comunicados").text and TIT not in mc.get("/morador/comunicados").text
    assert pub.get(f"/comunicados/{cid}").status_code == 404 and mc.get(f"/morador/comunicados/{cid}").status_code == 404
    assert not enviados and not pushes

    # só condôminos: notifica 1x, aparece na área e no painel como novo, não no site
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "condominos"}); time.sleep(0.5)
    assert [e[0] for e in enviados] == ["ana@example.com"], enviados
    with SessionLocal() as db:
        c = db.get(Comunicado, cid); assert c.publicado_por == adm.login and c.publicado_ip
    assert "Publicado por" in ac.get("/admin/comunicados").text
    assert TIT in enviados[0][1] and f"/morador/comunicados/{cid}" in enviados[0][2]
    assert pushes and pushes[0]["tag"] == "comunicado" and pushes[0]["url"].endswith(str(cid))
    painel = mc.get("/morador").text; assert "1 comunicado(s) novo(s)" in painel and "Comunicados (1)" in painel
    lst = mc.get("/morador/comunicados").text; assert TIT in lst and "· novo" in lst and "Setembro de 2026" in lst and "…" in lst and TEXTO not in lst
    assert "Comunicados (1)" not in mc.get("/morador").text  # badge zerado após abrir a lista
    assert TEXTO in mc.get(f"/morador/comunicados/{cid}").text
    assert TIT not in pub.get("/comunicados").text and TIT not in pub.get("/").text

    # público: home (antes dos cards de moradores), lista por mês, detalhe; sem reenvio
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "publico"}); time.sleep(0.3)
    assert len(enviados) == 1 and len(pushes) == 1
    home = pub.get("/").text; assert TIT in home and home.index("Comunicados públicos") < home.index("Para moradores") and TEXTO not in home
    lst = pub.get("/comunicados").text; assert TIT in lst and "Setembro de 2026" in lst
    assert TEXTO in pub.get(f"/comunicados/{cid}").text

    # edição e volta a rascunho
    ac.post(f"/admin/comunicados/{cid}", data={"titulo": TIT + " 2", "texto": "novo texto"})
    assert TIT + " 2" in pub.get(f"/comunicados/{cid}").text
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "rascunho"})
    assert pub.get(f"/comunicados/{cid}").status_code == 404 and TIT not in pub.get("/").text
    assert ac.post("/admin/comunicados", data={"titulo": "", "texto": "x"}).status_code == 400
    ac.post(f"/admin/comunicados/{cid}/excluir")
    assert ac.get(f"/admin/comunicados/{cid}").status_code == 404
    print("check_comunicados ok")
finally:
    limpar()
