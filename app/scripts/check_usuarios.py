"""Checagem de usuários da administração: só mestre gerencia; usuário comum só entra nas áreas liberadas. Limpa o que cria:
docker exec condominio-app python scripts/check_usuarios.py"""
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser

LOGIN, COMUM = "teste.usuario", "comum.teste"


def cliente(uid):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(uid))); return c


def limpar():
    with SessionLocal() as db:
        db.execute(delete(AdminUser).where(AdminUser.login.in_([LOGIN, COMUM]))); db.commit()


limpar()
try:
    with SessionLocal() as db:
        mestre = db.scalar(select(AdminUser).where(AdminUser.master))
        db.add(AdminUser(login=COMUM, nome="Comum", senha_hash=auth.hash_senha("senha12345"), master=False,
                         areas=["moradores", "documentos", "comunicados", "financeiro", "assembleias", "interfone"])); db.commit()
        comum = db.scalar(select(AdminUser).where(AdminUser.login == COMUM))
    mc, cc = cliente(mestre.id), cliente(comum.id)
    # só mestre vê/usa usuários
    assert mc.get("/admin/usuarios").status_code == 200 and "/admin/usuarios" in mc.get("/admin").text
    assert cc.get("/admin/usuarios").status_code == 403 and "/admin/usuarios" not in cc.get("/admin").text
    # criar usuário com uma área
    assert mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "Teste Usuário", "senha": "senha12345", "area_documentos": "1"}, follow_redirects=False).status_code == 303
    assert mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "Dup", "senha": "senha12345"}).status_code == 400
    assert cc.post("/admin/usuarios", data={"login": "x.y", "nome": "X", "senha": "senha12345"}).status_code == 403
    for ruim in ("admin", "maria_silva", "maria.silva1", ".maria", "maria."):
        assert mc.post("/admin/usuarios", data={"login": ruim, "nome": "X", "senha": "senha12345"}).status_code == 400, ruim
    with SessionLocal() as db:
        u = db.scalar(select(AdminUser).where(AdminUser.login == LOGIN)); assert not u.master and u.areas == ["documentos"]
    uc = cliente(u.id)
    assert uc.get("/admin").status_code == 200 and uc.get("/admin/documentos").status_code == 200
    for rota in ("/admin/moradores", "/admin/comunicados", "/admin/financeiro", "/admin/assembleias", "/admin/interfone", "/admin/usuarios"):
        assert uc.get(rota).status_code == 403, rota
    assert uc.post("/admin/comunicados", data={"titulo": "x", "texto": "y"}).status_code == 403
    menu = uc.get("/admin").text; assert "/admin/documentos" in menu and "/admin/moradores" not in menu and "/admin/usuarios" not in menu
    # login real com a senha criada
    lc = TestClient(app, base_url="https://t"); cp = auth.captcha_novo()
    r = lc.post("/admin/login", data={"login": LOGIN, "senha": "senha12345", "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}, follow_redirects=False)
    assert r.status_code == 303
    # mestre libera outra área; usuário passa a acessar
    mc.post(f"/admin/usuarios/{u.id}/areas", data={"area_documentos": "1", "area_comunicados": "1"})
    assert uc.get("/admin/comunicados").status_code == 200 and uc.get("/admin/moradores").status_code == 403
    # mestre não pode ser editado/excluído por aqui; usuário comum some ao excluir
    assert mc.post(f"/admin/usuarios/{mestre.id}/excluir").status_code == 404
    assert mc.post(f"/admin/usuarios/{u.id}/senha", data={"senha": "curta"}).status_code == 400
    assert mc.post(f"/admin/usuarios/{u.id}/excluir", follow_redirects=False).status_code == 303
    assert uc.get("/admin", follow_redirects=False).status_code == 303  # sessão cai
    with SessionLocal() as db:
        u2 = db.get(AdminUser, u.id); assert u2 and u2.excluido_em and u2.excluido_por == mestre.login and u2.excluido_ip  # desativado, não apagado
    cp = auth.captcha_novo()
    assert "incorretos" in lc.post("/admin/login", data={"login": LOGIN, "senha": "senha12345", "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}).text
    assert "usuário(s) desativado(s)" in mc.get("/admin/usuarios").text
    assert mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "De novo", "senha": "senha12345"}).status_code == 400  # login continua reservado
    print("check_usuarios ok")
finally:
    limpar()
