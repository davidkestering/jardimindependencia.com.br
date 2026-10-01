"""Interfone: tabela das unidades com interfone ativo e se estão online. A administração vê Portaria, Administração e todos os
apartamentos com condômino aprovado; o condômino só vê Portaria e Administração (não a presença dos vizinhos).
Conta da portaria (usuario.portaria): usuário da administração só com a área Interfone, que fala como a unidade Portaria.
Limpa o que cria: docker exec condominio-app python scripts/check_interfone_lista.py"""
import sys
sys.path.insert(0, "/app")
import mail, apns
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria
apns.notificar = lambda *a, **k: None  # teste nunca manda push aos condôminos

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
import interfone as ifone
from db import SessionLocal
from main import app
from models import LOGIN_PORTARIA, AdminUser, DispositivoApp, Morador, Unidade
from routers.interfone import unidade_da_sessao
from termo import TERMO

A, B = "52998224725", "11144477735"
TOKEN = "token-teste-lista-interfone"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(DispositivoApp).where(DispositivoApp.token == TOKEN))
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B])))
        db.execute(delete(AdminUser).where(AdminUser.login == LOGIN_PORTARIA, AdminUser.criado_por == "teste")); db.commit()  # só a conta criada pelo teste


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
u1 = None
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        ocupadas = select(Morador.unidade_id).where(Morador.status.in_(("pendente", "aprovado")))
        u1, u2, u3 = db.scalars(select(Unidade).where(Unidade.ativa, Unidade.apto != "", ~Unidade.id.in_(ocupadas))
                                .order_by(Unidade.bloco, Unidade.apto).limit(3)).all()  # três unidades livres (a 3ª fica sem condômino)
        r1, r2, r3 = u1.rotulo, u2.rotulo, u3.rotulo
        for u, nome, cpf in ((u1, "Ana Ifone", A), (u2, "Bia Ifone", B)):
            db.add(Morador(unidade_id=u.id, nome=nome, cpf=cpf, nascimento=auth.parse_data("1980-05-10"), email="a@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.flush()
        ma, mb = [db.scalar(select(Morador).where(Morador.cpf == c)) for c in (A, B)]
        db.add(DispositivoApp(morador_id=ma.id, token=TOKEN, plataforma="android")); db.commit()
        idb = mb.id
    ifone.conexoes[u1.id] = {object()}  # u1 com uma conexão aberta: online
    ac, cb = cliente("admin", adm.id), cliente("morador", idb)
    ON, OFF = '<span class="st st-on">Online</span>', '<span class="st st-off">Offline</span>'

    # administração: Portaria e Administração primeiro, depois os apartamentos com condômino aprovado, em ordem
    pg = ac.get("/admin/interfone").text
    assert f"<td>{r1}</td><td>{ON}</td><td>App Android</td>" in pg, "apto online com app"
    assert f"<td>{r2}</td><td>{OFF}</td><td>Não</td>" in pg, "apto offline sem aparelho"
    assert f"<td>Administração</td><td>{ON}</td>" in pg and "<td>Portaria</td>" in pg  # quem está vendo a página conta como online
    assert f"<td>{r3}</td>" not in pg  # sem condômino aprovado não tem interfone ativo
    assert pg.index("<td>Portaria</td>") < pg.index("<td>Administração</td>") < pg.index(f"<td>{r1}</td>") < pg.index(f"<td>{r2}</td>")

    # condômino: só Portaria e Administração; nada sobre os vizinhos
    pm = cb.get("/morador/interfone").text
    assert "<td>Portaria</td>" in pm and "<td>Administração</td>" in pm
    assert f"<td>{r1}</td>" not in pm and f"<td>{r2}</td>" not in pm and "App Android" not in pm
    # portaria: entra pela administração, só no interfone, e fala como a unidade Portaria (não como Administração)
    with SessionLocal() as db:
        por = db.scalar(select(AdminUser).where(AdminUser.login == LOGIN_PORTARIA))
        if not por:  # a conta real ainda não existe: usa uma de teste, apagada no fim
            por = AdminUser(login=LOGIN_PORTARIA, nome="Portaria teste", senha_hash=auth.hash_senha("senha-de-teste"), areas=["interfone"], criado_por="teste")
            db.add(por); db.commit()
        assert por.portaria and not adm.portaria and por.areas == ["interfone"] and not por.master
        ids = {b: db.scalar(select(Unidade.id).where(Unidade.bloco == b)) for b in ("PORTARIA", "ADMINISTRACAO")}
        assert unidade_da_sessao({"t": "admin", "id": str(por.id)}, db) == ids["PORTARIA"]
        assert unidade_da_sessao({"t": "admin", "id": str(adm.id)}, db) == ids["ADMINISTRACAO"]
        pid = por.id
    pc = cliente("admin", pid)
    pp = pc.get("/admin/interfone").text
    assert "falando como <strong>PORTARIA</strong>" in pp and f"<td>Portaria</td><td>{ON}</td>" in pp
    assert 'data-chamar="ADMINISTRACAO"' in pp and 'data-chamar="PORTARIA"' not in pp  # não liga para si mesma
    assert f"<td>{r1}</td><td>{ON}</td>" in pp  # a portaria vê quais apartamentos estão online
    assert pc.get("/admin/moradores").status_code == 403 and pc.get("/admin/usuarios").status_code == 403  # só o interfone
    print("check_interfone_lista ok")
finally:
    if u1:
        ifone.conexoes.pop(u1.id, None)
    limpar()
