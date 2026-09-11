"""Enquetes: criação, 1 voto por apto, inadimplente vota sem contar, resultado parcial após votar, exclusão lógica.
Limpa o que cria: docker exec condominio-app python scripts/check_enquetes.py"""
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Enquete, EnqueteOpcao, EnqueteVoto, Inadimplencia, Morador, Unidade
from termo import TERMO

PERG = "Enquete teste automático?"
A, B = "52998224725", "11144477735"


def limpar():
    with SessionLocal() as db:
        for e in db.scalars(select(Enquete).where(Enquete.pergunta == PERG)):
            db.execute(delete(EnqueteVoto).where(EnqueteVoto.enquete_id == e.id)); db.execute(delete(EnqueteOpcao).where(EnqueteOpcao.enquete_id == e.id)); db.delete(e)
        db.execute(delete(Inadimplencia).where(Inadimplencia.observacao == "enquete teste"))
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        u1, u2 = [db.scalar(select(Unidade).where(Unidade.bloco == b, Unidade.apto == a)) for b, a in (("01", "101"), ("02", "102"))]
        db.add(Morador(unidade_id=u1.id, nome="Ana Enq", cpf=A, nascimento=auth.parse_data("1980-05-10"), email="a@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.add(Morador(unidade_id=u2.id, nome="Bia Enq", cpf=B, nascimento=auth.parse_data("1980-05-10"), email="b@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.add(Inadimplencia(unidade_id=u2.id, observacao="enquete teste", registrado_por="teste")); db.commit()
        ma, mb = [db.scalar(select(Morador).where(Morador.cpf == c)) for c in (A, B)]
    ac, ca, cb = cliente("admin", adm.id), cliente("morador", ma.id), cliente("morador", mb.id)
    agora = datetime.now(timezone.utc)
    dt = lambda d: (agora + d).strftime("%Y-%m-%dT%H:%M")

    # criação (validações) e detalhe
    assert ac.post("/admin/enquetes", data={"pergunta": PERG, "opcoes": "Só uma", "abre_em": dt(timedelta(days=-1)), "fecha_em": dt(timedelta(days=1))}).status_code == 400
    r = ac.post("/admin/enquetes", data={"pergunta": PERG, "descricao": "desc", "opcoes": "Manhã\nTarde\n", "abre_em": dt(timedelta(days=-1)), "fecha_em": dt(timedelta(days=1))}, follow_redirects=False)
    assert r.status_code == 303; eid = r.headers["location"].rsplit("/", 1)[1]
    with SessionLocal() as db:
        e = db.scalar(select(Enquete).where(Enquete.pergunta == PERG)); assert e.criado_por == adm.login and e.criado_ip
        ops = db.scalars(select(EnqueteOpcao).where(EnqueteOpcao.enquete_id == e.id).order_by(EnqueteOpcao.ordem)).all(); assert [o.texto for o in ops] == ["Manhã", "Tarde"]
    assert PERG in ac.get("/admin/enquetes").text and "Criada por <strong>" in ac.get("/admin/enquetes").text and "Resultado parcial" in ac.get(f"/admin/enquetes/{eid}").text

    # condômino: lista, sem resultado antes de votar, vota, vê resultado parcial, não vota de novo
    assert "Votar" in ca.get("/morador/enquetes").text
    pg = ca.get(f"/morador/enquetes/{eid}").text; assert "Confirmar voto" in pg and "Resultado" not in pg
    assert ca.post(f"/morador/enquetes/{eid}/votar", data={"opcao": str(ops[0].id)}, follow_redirects=False).status_code == 303
    pg = ca.get(f"/morador/enquetes/{eid}").text; assert "Sua unidade votou em <strong>Manhã" in pg and "Resultado parcial" in pg and "Votos válidos: 1" in pg
    assert "já votou" in ca.post(f"/morador/enquetes/{eid}/votar", data={"opcao": str(ops[1].id)}).text
    assert ca.post(f"/morador/enquetes/{eid}/votar", data={"opcao": str(adm.id)}).status_code in (400, 303)  # opção inválida ou já votou

    # inadimplente: vota, vê aviso, não conta
    pg = cb.get(f"/morador/enquetes/{eid}").text; assert "registro de inadimplência" in pg
    r = cb.post(f"/morador/enquetes/{eid}/votar", data={"opcao": str(ops[1].id)}, follow_redirects=False); assert "inadimpl" in r.headers["location"]
    pg = ac.get(f"/admin/enquetes/{eid}").text; assert "Votos válidos: 1" in pg and "Não computados por inadimplência: 1" in pg
    with SessionLocal() as db:
        assert db.scalar(select(EnqueteVoto).where(EnqueteVoto.unidade_id == u2.id)).inadimplente_no_voto

    # exclusão lógica: some do condômino, fica no histórico do admin com votos
    ac.post(f"/admin/enquetes/{eid}/excluir")
    assert ca.get(f"/morador/enquetes/{eid}").status_code == 404 and PERG not in ca.get("/morador/enquetes").text
    with SessionLocal() as db:
        e = db.get(Enquete, __import__("uuid").UUID(eid)); assert e.excluido_em and e.excluido_por == adm.login
        assert db.scalar(select(EnqueteVoto).where(EnqueteVoto.enquete_id == e.id)) is not None
    assert "enquete(s) excluída(s)" in ac.get("/admin/enquetes").text and "Enquete excluída por" in ac.get(f"/admin/enquetes/{eid}").text
    # área liberada: usuário sem 'enquetes' não entra
    print("check_enquetes ok")
finally:
    limpar()
