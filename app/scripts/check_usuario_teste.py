"""Checagem do usuário de teste (usuario.apple e usuario.android, revisão das lojas): entra em todas as áreas, aprova cadastros e responde
ocorrências como qualquer administrador, mas só altera ou exclui o que ele mesmo criou, e publicar comunicado não notifica os condôminos. Limpa o que cria:
docker exec -e SENHA_TESTE=... condominio-app python scripts/check_usuario_teste.py"""
import os
import sys
from datetime import date, datetime, timedelta, timezone
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
import interfone
from db import SessionLocal
from main import app
from models import (AREAS_ADMIN, LOGIN_TESTE, AdminUser, Assembleia, Comunicado, Documento, Enquete, Inadimplencia, Morador,
                    OCUPA_APTO, Ocorrencia, OcorrenciaMensagem, Pauta, Unidade)
from routers import comunicados

MESTRE_LOGIN = "mestre.checagem"
CPF_M, CPF_T, CPF_O = "52998224725", "11144477735", "12345678909"
agora = datetime.now(timezone.utc)
LOGIN_TESTE = os.environ.get("LOGIN_TESTE", LOGIN_TESTE)  # para conferir a outra conta de teste: -e LOGIN_TESTE=usuario.android
SENHA_TESTE = os.environ.get("SENHA_TESTE")  # senha real da conta de teste: nunca no repositório
notificados = []
comunicados.notificar_comunicado = lambda cid: notificados.append(cid)


def cliente(uid, html=False):
    c = TestClient(app, base_url="https://t", headers={"accept": "text/html"} if html else {})
    c.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(uid)))
    return c


def limpar():
    with SessionLocal() as db:
        db.execute(delete(OcorrenciaMensagem).where(OcorrenciaMensagem.autor.in_([LOGIN_TESTE, MESTRE_LOGIN])))
        db.execute(delete(Ocorrencia).where(Ocorrencia.titulo == "chk-teste"))
        db.execute(delete(Morador).where(Morador.cpf.in_([CPF_M, CPF_T, CPF_O])))
        for M, col in ((Comunicado, Comunicado.autor), (Documento, Documento.enviado_por), (Enquete, Enquete.criado_por),
                       (Pauta, Pauta.criado_por), (Assembleia, Assembleia.criado_por), (Inadimplencia, Inadimplencia.registrado_por)):
            db.execute(delete(M).where(col == MESTRE_LOGIN))
            db.execute(delete(M).where((col == LOGIN_TESTE)))
        db.execute(delete(AdminUser).where(AdminUser.login == MESTRE_LOGIN))
        db.commit()


limpar()
try:
    with SessionLocal() as db:
        teste = db.scalar(select(AdminUser).where(AdminUser.login == LOGIN_TESTE, AdminUser.excluido_em.is_(None)))
        assert teste and teste.teste and not teste.master and set(teste.areas) == set(AREAS_ADMIN), "usuario.apple deve existir, não mestre, com todas as áreas"
        if SENHA_TESTE:
            assert auth.verificar_senha(SENHA_TESTE, teste.senha_hash)
        db.add(AdminUser(login=MESTRE_LOGIN, nome="Mestre", senha_hash=auth.hash_senha("senha12345"), master=True)); db.commit()
        mestre = db.scalar(select(AdminUser).where(AdminUser.login == MESTRE_LOGIN))
        assert not mestre.teste
        ocupadas = select(Morador.unidade_id).where(Morador.status.in_(OCUPA_APTO))
        u1, u2, u3, u4 = db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.id.not_in(ocupadas))
                                    .order_by(Unidade.bloco.desc(), Unidade.apto.desc()).limit(4)).all()
        # registros "oficiais" (do mestre) e um pendente vindo do site
        db.add_all([
            Comunicado(titulo="oficial", texto="x", autor=MESTRE_LOGIN),
            Documento(titulo="oficial", categoria="Atas", arquivo="x/y.pdf", nome_original="y.pdf", competencia=date.today(), enviado_por=MESTRE_LOGIN),
            Enquete(pergunta="oficial?", abre_em=agora, fecha_em=agora + timedelta(days=1), criado_por=MESTRE_LOGIN),
            Assembleia(titulo="oficial", abre_em=agora, fecha_em=agora + timedelta(days=1), criado_por=MESTRE_LOGIN),
            Inadimplencia(unidade_id=u1.id, observacao="x", registrado_por=MESTRE_LOGIN),
            Morador(unidade_id=u2.id, nome="Site Pendente", cpf=CPF_M, nascimento=date(1990, 1, 1), email="a@b.c", telefone="9", status="pendente", origem="site"),
        ]); db.commit()
        co, do_, en, asm, ina = [db.scalar(select(M).where(col == MESTRE_LOGIN)) for M, col in
                                 ((Comunicado, Comunicado.autor), (Documento, Documento.enviado_por), (Enquete, Enquete.criado_por),
                                  (Assembleia, Assembleia.criado_por), (Inadimplencia, Inadimplencia.registrado_por))]
        pend = db.scalar(select(Morador).where(Morador.cpf == CPF_M))
        db.add(Pauta(assembleia_id=asm.id, texto="p", criado_por=MESTRE_LOGIN)); db.commit()
        pa = db.scalar(select(Pauta).where(Pauta.criado_por == MESTRE_LOGIN))
        # ocorrência de um condômino
        db.add(Morador(unidade_id=u3.id, nome="Cond", cpf=CPF_O, nascimento=date(1990, 1, 1), email="c@b.c", telefone="9", status="aprovado")); db.commit()
        mo = db.scalar(select(Morador).where(Morador.cpf == CPF_O))
        db.add(Ocorrencia(unidade_id=u3.id, morador_id=mo.id, titulo="chk-teste")); db.commit()
        oc = db.scalar(select(Ocorrencia).where(Ocorrencia.titulo == "chk-teste"))
        ids = dict(co=co.id, do_=do_.id, en=en.id, asm=asm.id, pa=pa.id, ina=ina.id, pend=pend.id, oc=oc.id, u4=(u4.bloco, u4.apto))

    tc, mc = cliente(teste.id), cliente(mestre.id)
    if SENHA_TESTE:  # login real
        lc = TestClient(app, base_url="https://t"); cp = auth.captcha_novo()
        r = lc.post("/admin/login", data={"login": LOGIN_TESTE, "senha": SENHA_TESTE, "captcha_token": cp["token"],
                                          "captcha": str(auth._captcha.loads(cp["token"])["r"])}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin"
    # navega em tudo, menos usuários/histórico
    for area in AREAS_ADMIN:
        assert tc.get(f"/admin/{area}").status_code == 200, area
    assert tc.get("/admin/usuarios").status_code == 403 and tc.get("/admin/historico").status_code == 403

    # NÃO mexe no que é oficial (403 em todas as rotas de alteração/exclusão)
    bloqueadas = [
        (f"/admin/comunicados/{ids['co']}", {"titulo": "h", "texto": "h"}),
        (f"/admin/comunicados/{ids['co']}/visibilidade", {"visibilidade": "publico"}),
        (f"/admin/comunicados/{ids['co']}/excluir", {}),
        (f"/admin/documentos/{ids['do_']}/publico", {"publico": "1"}),
        (f"/admin/documentos/{ids['do_']}/competencia", {"competencia": "2024-01-01", "justificativa": "teste teste"}),
        (f"/admin/documentos/{ids['do_']}/categoria", {"categoria": "Atas", "justificativa": "teste teste"}),
        (f"/admin/documentos/{ids['do_']}/excluir", {"justificativa": "teste teste"}),
        (f"/admin/enquetes/{ids['en']}/excluir", {}),
        (f"/admin/assembleias/{ids['asm']}/pautas", {"texto": "p", "opcoes": "a\nb"}),
        (f"/admin/assembleias/{ids['asm']}/pautas/{ids['pa']}/excluir", {}),
        (f"/admin/assembleias/{ids['asm']}/excluir", {}),
        (f"/admin/financeiro/{ids['ina']}/encerrar", {}),
    ]
    for rota, dados in bloqueadas:
        r = tc.post(rota, data=dados, follow_redirects=False)
        assert r.status_code == 403, (rota, r.status_code)
    # página amigável quando o navegador pede HTML
    r = cliente(teste.id, html=True).post(f"/admin/comunicados/{ids['co']}/excluir", follow_redirects=False)
    assert r.status_code == 403 and "Ação não permitida" in r.text and "criados por ele mesmo" in r.text
    with SessionLocal() as db:  # nada mudou
        assert db.get(Comunicado, ids["co"]).titulo == "oficial" and db.get(Comunicado, ids["co"]).visibilidade == "rascunho"
        assert db.get(Documento, ids["do_"]).excluido_em is None and db.get(Assembleia, ids["asm"]).excluido_em is None
        assert db.get(Inadimplencia, ids["ina"]).encerrado_em is None
    assert not notificados

    # Aprova cadastro alheio e responde ocorrência como qualquer administrador (fluxo exigido na revisão da App Store)
    assert tc.post(f"/admin/moradores/{ids['pend']}/status", data={"status": "aprovado"}, follow_redirects=False).status_code == 303
    assert tc.post(f"/admin/ocorrencias/{ids['oc']}/mensagem", data={"texto": "oi"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.get(Morador, ids["pend"]).status == "aprovado" and db.get(Morador, ids["pend"]).decidido_por == LOGIN_TESTE
        assert db.scalar(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == ids["oc"])).autor == LOGIN_TESTE

    # Mexe no que ele mesmo criou
    assert tc.post("/admin/comunicados", data={"titulo": "meu", "texto": "t"}, follow_redirects=False).status_code == 303
    assert tc.post("/admin/moradores", data={"nome": "Meu Morador", "cpf": CPF_T, "nascimento": "01/01/1990", "bloco": ids["u4"][0],
                                             "apto": ids["u4"][1], "email": "m@b.c", "telefone": "91999999999"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        meu = db.scalar(select(Comunicado).where(Comunicado.autor == LOGIN_TESTE))
        meu_m = db.scalar(select(Morador).where(Morador.cpf == CPF_T))
        assert meu and meu_m and meu_m.decidido_por == LOGIN_TESTE and meu_m.status == "aprovado"
    assert tc.post(f"/admin/moradores/{meu_m.id}/status", data={"status": "negado"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        assert db.get(Morador, meu_m.id).status == "negado"
    assert tc.post(f"/admin/comunicados/{meu.id}", data={"titulo": "meu2", "texto": "t"}, follow_redirects=False).status_code == 303
    assert tc.post(f"/admin/comunicados/{meu.id}/visibilidade", data={"visibilidade": "publico"}, follow_redirects=False).status_code == 303
    assert not notificados, "publicação do usuário de teste não pode notificar os condôminos"
    assert tc.post(f"/admin/comunicados/{meu.id}/excluir", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        meu = db.get(Comunicado, meu.id); assert meu.titulo == "meu2" and meu.visibilidade == "publico" and meu.excluido_em
    # mestre publicando notifica normalmente
    assert mc.post(f"/admin/comunicados/{ids['co']}/visibilidade", data={"visibilidade": "condominos"}, follow_redirects=False).status_code == 303
    assert notificados == [ids["co"]]
    # mestre continua podendo excluir o oficial
    assert mc.post(f"/admin/comunicados/{ids['co']}/excluir", follow_redirects=False).status_code == 303
    print("usuario de teste ok")
finally:
    limpar()
