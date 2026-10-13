"""Checagem de usuários da administração: só mestre gerencia; usuário comum só faz, em cada área, as ações liberadas
(matriz área × ações em /admin/usuarios/{id}). Limpa o que cria: docker exec condominio-app python scripts/check_usuarios.py"""
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
from models import ACOES_ADMIN, AREAS_ADMIN, AdminUser, Documento, Morador, Ocorrencia, OcorrenciaAnexo, OcorrenciaMensagem, permissoes_totais
from termo import TERMO
from unidades_teste import unidades

LOGIN, COMUM, LEITOR = "teste.usuario", "comum.teste", "leitor.teste"
CPF, TIT = "52998224725", "Ocorrência teste permissões"


def cliente(uid):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(uid))); return c


def limpar():
    with SessionLocal() as db:
        db.execute(delete(AdminUser).where(AdminUser.login.in_([LOGIN, COMUM, LEITOR])))
        for d in db.scalars(select(Documento).where(Documento.titulo == TIT)):
            (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True); db.delete(d)
        for o in db.scalars(select(Ocorrencia).where(Ocorrencia.titulo == TIT)):  # mensagens e anexos caem em cascata no banco
            for m in db.scalars(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == o.id)):
                for a in db.scalars(select(OcorrenciaAnexo).where(OcorrenciaAnexo.mensagem_id == m.id)):
                    (Path(UPLOAD_DIR) / a.arquivo).unlink(missing_ok=True)
            db.delete(o)
        db.flush()  # apaga a ocorrência antes do condômino (o banco também a apagaria em cascata, e o ORM avisaria)
        db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()


limpar()
try:
    with SessionLocal() as db:
        mestre = db.scalar(select(AdminUser).where(AdminUser.master))
        db.add(AdminUser(login=COMUM, nome="Comum", senha_hash=auth.hash_senha("senha12345"), master=False,
                         areas=permissoes_totais(["moradores", "documentos", "comunicados", "financeiro", "assembleias", "interfone"])))
        # o cenário-alvo: só visualiza e baixa documentos, mas responde ocorrências (com anexo)
        db.add(AdminUser(login=LEITOR, nome="Leitor", senha_hash=auth.hash_senha("senha12345"), master=False,
                         areas={"documentos": ["ver"], "ocorrencias": ["ver", "responder"]}))
        u1, = unidades(db)
        db.add(Morador(unidade_id=u1.id, nome="Ana Perm", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.commit()
        comum, leitor = [db.scalar(select(AdminUser).where(AdminUser.login == l)) for l in (COMUM, LEITOR)]
        ma = db.scalar(select(Morador).where(Morador.cpf == CPF))
        o = Ocorrencia(unidade_id=u1.id, morador_id=ma.id, titulo=TIT, termo_texto=TERMO); db.add(o); db.commit()
        db.add(OcorrenciaMensagem(ocorrencia_id=o.id, autor_tipo="morador", autor=ma.nome, texto="Lâmpada queimada.")); db.commit()
        oid = o.id
    mc, cc, lc = cliente(mestre.id), cliente(comum.id), cliente(leitor.id)
    # só mestre vê/usa usuários
    assert mc.get("/admin/usuarios").status_code == 200 and "/admin/usuarios" in mc.get("/admin").text
    assert cc.get("/admin/usuarios").status_code == 403 and "/admin/usuarios" not in cc.get("/admin").text
    assert cc.get(f"/admin/usuarios/{leitor.id}").status_code == 403 and mc.get(f"/admin/usuarios/{leitor.id}").status_code == 200
    # criar usuário: nasce sem permissão e cai na página da matriz
    r = mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "Teste Usuário", "senha": "senha12345"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/usuarios/")
    assert mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "Dup", "senha": "senha12345"}).status_code == 400
    assert cc.post("/admin/usuarios", data={"login": "x.y", "nome": "X", "senha": "senha12345"}).status_code == 403
    for ruim in ("admin", "maria_silva", "maria.silva1", ".maria", "maria."):
        assert mc.post("/admin/usuarios", data={"login": ruim, "nome": "X", "senha": "senha12345"}).status_code == 400, ruim
    with SessionLocal() as db:
        u = db.scalar(select(AdminUser).where(AdminUser.login == LOGIN)); assert not u.master and u.areas == {}
        assert u.criado_por == mestre.login and u.criado_ip and u.criado_em
    pagina = mc.get(f"/admin/usuarios/{u.id}").text
    assert all(f'name="p_{a}_{ac}"' in pagina for a, acoes in ACOES_ADMIN.items() for ac in acoes) and 'name="p_documentos_responder"' not in pagina
    uc = cliente(u.id)
    assert uc.get("/admin").status_code == 200 and "nenhuma permissão" in uc.get("/admin").text
    for rota in ("/admin/moradores", "/admin/documentos", "/admin/usuarios"):
        assert uc.get(rota).status_code == 403, rota
    # mestre libera só "ver" em documentos e "responder" em ocorrências: "ver" de ocorrências entra sozinho; ação desconhecida é ignorada
    r = mc.post(f"/admin/usuarios/{u.id}/areas", data={"p_documentos_ver": "1", "p_ocorrencias_responder": "1", "p_garagem_zzz": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/usuarios/{u.id}?ok=1"
    with SessionLocal() as db:
        u3 = db.get(AdminUser, u.id); assert u3.areas == {"documentos": ["ver"], "ocorrencias": ["ver", "responder"]}, u3.areas
        assert u3.alterado_por == mestre.login and u3.alterado_ip and u3.alterado_em
    assert "alterado por" in mc.get("/admin/usuarios").text and "Ocorrências: Visualizar, Responder" in mc.get("/admin/usuarios").text
    assert uc.get("/admin/documentos").status_code == 200 and uc.get("/admin/ocorrencias").status_code == 200 and uc.get("/admin/moradores").status_code == 403
    menu = uc.get("/admin").text; assert "/admin/documentos" in menu and "/admin/ocorrencias" in menu and "/admin/moradores" not in menu and "/admin/usuarios" not in menu
    # login real com a senha criada
    lg = TestClient(app, base_url="https://t"); cp = auth.captcha_novo()
    r = lg.post("/admin/login", data={"login": LOGIN, "senha": "senha12345", "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}, follow_redirects=False)
    assert r.status_code == 303

    # ---- cenário-alvo (LEITOR): documentos só leitura; ocorrências com resposta e anexo ----
    pdf = b"%PDF-1.4 teste permissoes"
    assert lc.post("/admin/documentos", data={"categoria": "Outros", "titulo": TIT}, files=[("arquivos", ("perm.pdf", pdf, "application/pdf"))]).status_code == 403
    assert lc.post("/admin/categorias", data={"nome": "Cat perm"}).status_code == 403
    r = mc.post("/admin/documentos", data={"categoria": "Outros", "titulo": TIT}, files=[("arquivos", ("perm.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert r.status_code == 303
    with SessionLocal() as db:
        doc = db.scalar(select(Documento).where(Documento.titulo == TIT)); assert doc
    lista = lc.get("/admin/documentos").text
    assert f"/admin/documentos/{doc.id}" in lista and 'id="f-docs"' not in lista and 'class="btn sec excluir-doc"' not in lista and 'class="btn sec alterar-comp"' not in lista and "/publico" not in lista
    assert lc.get(f"/admin/documentos/{doc.id}").content == pdf  # baixar faz parte de visualizar
    for rota, dados in ((f"/admin/documentos/{doc.id}/publico", {"publico": "1"}), (f"/admin/documentos/{doc.id}/competencia", {"competencia": "2024-01-01", "justificativa": "teste perm"}),
                        (f"/admin/documentos/{doc.id}/categoria", {"categoria": "Outros", "justificativa": "teste perm"}), (f"/admin/documentos/{doc.id}/excluir", {"justificativa": "teste perm"})):
        r = lc.post(rota, data=dados); assert r.status_code == 403 and "Documentos" in r.text, rota
    lista_m = mc.get("/admin/documentos").text; assert 'id="f-docs"' in lista_m and 'class="btn sec excluir-doc"' in lista_m  # mestre segue vendo tudo
    pag = lc.get(f"/admin/ocorrencias/{oid}").text; assert "/mensagem" in pag and "Responder" in pag
    r = lc.post(f"/admin/ocorrencias/{oid}/mensagem", data={"texto": "Troca agendada."}, files=[("arquivos", ("os.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert r.status_code == 303 and "erro" not in r.headers["location"], r.headers["location"]
    with SessionLocal() as db:
        resp = db.scalar(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == oid, OcorrenciaMensagem.autor_tipo == "admin"))
        assert resp and resp.autor == LEITOR and len(resp.anexos) == 1
    # COMUM tem tudo nas áreas dele, mas nada em ocorrências; uc (igual ao leitor) não cria comunicado
    assert cc.post(f"/admin/ocorrencias/{oid}/mensagem", data={"texto": "x"}).status_code == 403
    assert uc.post("/admin/comunicados", data={"titulo": "x", "texto": "y"}).status_code == 403
    assert "Responder" in uc.get(f"/admin/ocorrencias/{oid}").text and 'id="f-docs"' not in uc.get("/admin/documentos").text

    # mestre não pode ser editado/excluído por aqui; usuário comum some ao excluir
    assert mc.get(f"/admin/usuarios/{mestre.id}").status_code == 404 and mc.post(f"/admin/usuarios/{mestre.id}/excluir").status_code == 404
    assert mc.post(f"/admin/usuarios/{u.id}/senha", data={"senha": "curta"}).status_code == 400
    assert mc.post(f"/admin/usuarios/{u.id}/excluir", follow_redirects=False).status_code == 303
    assert uc.get("/admin", follow_redirects=False).status_code == 303  # sessão cai
    with SessionLocal() as db:
        u2 = db.get(AdminUser, u.id); assert u2 and u2.excluido_em and u2.excluido_por == mestre.login and u2.excluido_ip  # desativado, não apagado
    cp = auth.captcha_novo()
    assert "incorretos" in lg.post("/admin/login", data={"login": LOGIN, "senha": "senha12345", "captcha_token": cp["token"], "captcha": str(auth._captcha.loads(cp["token"])["r"])}).text
    assert "usuário(s) desativado(s)" in mc.get("/admin/usuarios").text
    assert mc.post("/admin/usuarios", data={"login": LOGIN, "nome": "De novo", "senha": "senha12345"}).status_code == 400  # login continua reservado
    assert set(AREAS_ADMIN) == set(ACOES_ADMIN)
    print("check_usuarios ok")
finally:
    limpar()
