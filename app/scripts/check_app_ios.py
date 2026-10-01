"""Checagem dos ajustes para os apps iOS e Android: sem cabeçalho/rodapé no app, registro de token (APNs e FCM), sair. Roda no container e limpa o que cria:
docker exec condominio-app python scripts/check_app_ios.py"""
import random
import string
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
import auth
from db import SessionLocal
from main import app
from models import DispositivoApp, Morador
from termo import TERMO
from unidades_teste import unidades

CPF = "52998224725"
UA_APP = "Mozilla/5.0 (iPhone) JardimIndependenciaApp/1.0"
TOKEN = "ab" * 32
TOKEN_FCM = "".join(random.Random(1).choices(string.ascii_letters + string.digits + ":_-", k=4096))  # tamanho máximo aceito; aleatório para não comprimir no índice


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()  # dispositivo_app cai em cascata


def cliente(mid=None, ua=None):
    c = TestClient(app, base_url="https://t", headers={"User-Agent": ua} if ua else None)
    if mid:
        c.cookies.set(auth.COOKIE, auth.criar_sessao("morador", str(mid)))
    return c


limpar()
try:
    with SessionLocal() as db:
        u1, u2 = unidades(db, 2)
        for u in (u1, u2):
            db.add(Morador(unidade_id=u.id, nome="Ana Teste", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com",
                           telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.commit()
        m1, m2 = [db.scalar(select(Morador).where(Morador.cpf == CPF, Morador.unidade_id == u.id)) for u in (u1, u2)]

    # 1. cabeçalho e rodapé somem só para o app
    site, ap = cliente().get("/morador/login").text, cliente(ua=UA_APP).get("/morador/login").text
    assert "☰" in site and "CNPJ" in site and "Área do condômino" in site
    assert "☰" not in ap and "CNPJ" not in ap and "Área do condômino" in ap and "Solicite seu cadastro" in ap
    assert "CNPJ" in cliente().get("/privacidade").text and "Política de privacidade" in cliente(ua=UA_APP).get("/privacidade").text

    # 2. registro do token: 401 sem sessão, 400 inválido, 204 cria, upsert migra de morador
    dados = {"token": TOKEN, "plataforma": "ios", "ambiente": "sandbox"}
    assert cliente(ua=UA_APP).post("/morador/app/dispositivo", json=dados, headers={"X-Jardim-App": "1"}).status_code == 401
    assert cliente(m1.id).post("/morador/app/dispositivo", json={**dados, "token": "zz"}).status_code == 400
    assert cliente(m1.id).post("/morador/app/dispositivo", json={**dados, "ambiente": "x"}).status_code == 400
    assert cliente(m1.id).post("/morador/app/dispositivo", json=dados, headers={"X-Jardim-App": "1"}).status_code == 204
    with SessionLocal() as db:
        d = db.scalar(select(DispositivoApp).where(DispositivoApp.token == TOKEN)); assert d.morador_id == m1.id and d.ambiente == "sandbox"
    assert cliente(m2.id).post("/morador/app/dispositivo", json={**dados, "ambiente": "production"}).status_code == 204
    with SessionLocal() as db:
        ds = db.scalars(select(DispositivoApp).where(DispositivoApp.token == TOKEN)).all(); assert len(ds) == 1 and ds[0].morador_id == m2.id and ds[0].ambiente == "production"

    # 3. Android (FCM): token opaco e longo, maiúsculas preservadas; validação hexadecimal só vale para o iOS
    andr = {"token": TOKEN_FCM, "plataforma": "android", "ambiente": "production"}
    assert cliente(ua=UA_APP).post("/morador/app/dispositivo", json=andr, headers={"X-Jardim-App": "1"}).status_code == 401
    for ruim in ({**andr, "token": ""}, {**andr, "token": "x" * 4097}, {**andr, "token": "com espaço"}, {**andr, "plataforma": "windows"},
                 {**dados, "token": TOKEN_FCM}, [andr]):
        assert cliente(m1.id).post("/morador/app/dispositivo", json=ruim).status_code == 400, ruim
    assert cliente(m1.id).post("/morador/app/dispositivo", json=andr, headers={"X-Jardim-App": "1"}).status_code == 204
    assert cliente(m1.id).post("/morador/app/dispositivo", json=andr).status_code == 204  # repetir não duplica
    with SessionLocal() as db:
        ds = db.scalars(select(DispositivoApp).where(DispositivoApp.token == TOKEN_FCM)).all()
        assert len(ds) == 1 and (ds[0].morador_id, ds[0].plataforma, ds[0].ambiente) == (m1.id, "android", "production")
        assert db.scalar(select(func.count()).select_from(DispositivoApp).where(DispositivoApp.morador_id.in_([m1.id, m2.id]))) == 2  # iOS + Android

    # 4. sair: apaga os tokens do morador; no app vai para o login, no site para a home
    r = cliente(m2.id, ua=UA_APP).get("/morador/sair", follow_redirects=False); assert r.status_code == 303 and r.headers["location"] == "/morador/login"
    with SessionLocal() as db:
        assert db.scalar(select(DispositivoApp).where(DispositivoApp.token == TOKEN)) is None
    assert cliente(m1.id).get("/morador/sair", follow_redirects=False).headers["location"] == "/"
    with SessionLocal() as db:
        assert db.scalar(select(DispositivoApp).where(DispositivoApp.token == TOKEN_FCM)) is None  # o do Android também sai

    # excluir conta: apaga tokens de todos os aptos do CPF
    assert cliente(m1.id).post("/morador/app/dispositivo", json=dados).status_code == 204
    assert cliente(m2.id).post("/morador/excluir-conta", data={"confirmo": "sim"}).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(DispositivoApp).where(DispositivoApp.token == TOKEN)) is None
    print("check_app_ios ok")
finally:
    limpar()
