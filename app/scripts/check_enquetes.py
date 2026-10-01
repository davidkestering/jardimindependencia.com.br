"""Enquetes: criação, 1 voto por apto, inadimplente vota sem contar, resultado parcial após votar, exclusão lógica.
Lista nominal dos votos (unidade e voto) para a administração, em enquetes e em pautas de assembleia.
Limpa o que cria: docker exec condominio-app python scripts/check_enquetes.py"""
import sys
sys.path.insert(0, "/app")
import mail, apns
mail.enviar = lambda *a, **k: True
apns.notificar = lambda *a, **k: None  # teste nunca manda push aos condôminos
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Assembleia, Enquete, EnqueteOpcao, EnqueteVoto, Inadimplencia, Morador, Opcao, Pauta, Unidade
from termo import TERMO
from unidades_teste import unidades

PERG = "Enquete teste automático?"
TIT = "Assembleia lista de votos teste"
A, B = "52998224725", "11144477735"


def limpar():
    with SessionLocal() as db:
        for e in db.scalars(select(Enquete).where(Enquete.pergunta == PERG)):
            db.execute(delete(EnqueteVoto).where(EnqueteVoto.enquete_id == e.id)); db.execute(delete(EnqueteOpcao).where(EnqueteOpcao.enquete_id == e.id)); db.delete(e)
        db.execute(delete(Assembleia).where(Assembleia.titulo == TIT))  # pautas, opções e votos saem em cascata
        db.execute(delete(Inadimplencia).where(Inadimplencia.observacao == "enquete teste"))
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        u1, u2, u3 = unidades(db, 3)  # a 3ª não vota
        r1, r2, r3 = u1.rotulo, u2.rotulo, u3.rotulo
        total = db.scalar(select(func.count()).select_from(Unidade).where(Unidade.em_uso, Unidade.apto != ""))  # dentro do teste, inclui as de teste
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

    # não há voto secreto: a administração vê a unidade e o voto de cada uma
    U1, U2 = f"<td>{r1}</td><td><strong>{{}}</strong></td>", f"<td>{r2}</td><td><strong>{{}}</strong></td>"
    SEM = f"<td>{r3}</td><td></td><td></td><td></td><td></td>"  # todos os aptos aparecem, em ordem; quem não votou fica em branco
    assert pg.count("<td>Bloco ") == total and pg.index(r3) < pg.index(r2) < pg.index(r1) and SEM in pg  # aptos 997, 998 e 999, nesta ordem
    assert f"Votos por unidade (2 de {total})" in pg and U1.format("Manhã") in pg and U2.format("Tarde") in pg and "Ana Enq" in pg and pg.count("Não computado (inadimplência)") == 1

    # assembleia: a mesma lista, por pauta
    with SessionLocal() as db:
        asm = Assembleia(titulo=TIT, abre_em=agora - timedelta(days=1), fecha_em=agora + timedelta(days=1), criado_por="teste")
        db.add(asm); db.flush()
        p = Pauta(assembleia_id=asm.id, texto="Pauta teste", opcoes=[Opcao(ordem=1, texto="Aprovo"), Opcao(ordem=2, texto="Rejeito")])
        db.add(p); db.commit(); aid, pid, o1, o2 = asm.id, p.id, p.opcoes[0].id, p.opcoes[1].id
    assert f"Votos por unidade (0 de {total})" in ac.get(f"/admin/assembleias/{aid}").text
    ca.post(f"/morador/assembleias/{aid}/votar", data={f"pauta_{pid}": str(o1)}); cb.post(f"/morador/assembleias/{aid}/votar", data={f"pauta_{pid}": str(o2)})
    pg = ac.get(f"/admin/assembleias/{aid}").text
    assert pg.count("<td>Bloco ") == total and SEM in pg and f"Votos por unidade (2 de {total})" in pg and U1.format("Aprovo") in pg and U2.format("Rejeito") in pg and "Bia Enq" in pg and pg.count("Não computado (inadimplência)") == 1

    # o condômino só vê quantidades: nada de lista, nem a unidade ou o nome de quem votou
    for url in (f"/morador/enquetes/{eid}", f"/morador/assembleias/{aid}"):
        pm = ca.get(url).text; assert "Votos por unidade" not in pm and r2 not in pm and "Bia Enq" not in pm, url
    assert "Votos válidos: 1" in ca.get(f"/morador/enquetes/{eid}").text

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
