"""Ocorrências: registro imutável com anexos, e-mails (contato@ + cópia), resposta da administração com e-mail e aviso,
mensagem do condômino, finalização. Limpa o que cria: docker exec condominio-app python scripts/check_ocorrencias.py"""
import sys, time
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria

from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
from models import AdminUser, Morador, Ocorrencia, OcorrenciaAnexo, OcorrenciaMensagem, Unidade
from termo import TERMO, TERMO_OCORRENCIA
from urllib.parse import unquote_plus as unquote


def captcha(certo=True):
    cp = auth.captcha_novo(); r = auth._captcha.loads(cp["token"])["r"]
    return {"captcha_token": cp["token"], "captcha": str(r if certo else r + 1), "declaracao": "sim"}

A, TIT = "52998224725", "Ocorrência teste automático"


def limpar():
    with SessionLocal() as db:
        for o in db.scalars(select(Ocorrencia).where(Ocorrencia.titulo == TIT)):
            for m in db.scalars(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == o.id)):
                for a in db.scalars(select(OcorrenciaAnexo).where(OcorrenciaAnexo.mensagem_id == m.id)):
                    (Path(UPLOAD_DIR) / a.arquivo).unlink(missing_ok=True); db.delete(a)
                db.delete(m)
            db.delete(o)
        db.execute(delete(Morador).where(Morador.cpf == A)); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        u1 = db.scalar(select(Unidade).where(Unidade.bloco == "01", Unidade.apto == "101"))
        db.add(Morador(unidade_id=u1.id, nome="Ana Oc", cpf=A, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO)); db.commit()
        ma = db.scalar(select(Morador).where(Morador.cpf == A))
    ac, mc = cliente("admin", adm.id), cliente("morador", ma.id)
    pdf = b"%PDF-1.4 teste"

    # registro com anexo -> e-mail a contato@ e cópia ao condômino
    assert "verificação incorreta" in unquote(mc.post("/morador/ocorrencias", data={"titulo": TIT, "texto": "x", **captcha(False)}, follow_redirects=False).headers["location"])
    assert "aceitar a declaração" in unquote(mc.post("/morador/ocorrencias", data={"titulo": TIT, "texto": "x", **captcha(), "declaracao": ""}, follow_redirects=False).headers["location"])
    assert "art. 339" in mc.get("/morador/ocorrencias").text and "quanto é" in mc.get("/morador/ocorrencias").text
    r = mc.post("/morador/ocorrencias", data={"titulo": TIT, "texto": "Lâmpada queimada.", **captcha()}, files=[("arquivos", ("foto.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50, "image/png"))], follow_redirects=False)
    assert r.status_code == 303 and "/morador/ocorrencias/" in r.headers["location"]; oid = r.headers["location"].rsplit("/", 1)[1]
    with SessionLocal() as db:
        o = db.scalar(select(Ocorrencia).where(Ocorrencia.titulo == TIT)); assert o.numero and o.status == "aberta" and o.criado_ip
        assert o.termo_texto == TERMO_OCORRENCIA and o.termo_aceito_em and o.termo_ip
        msgs = db.scalars(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == o.id)).all(); assert len(msgs) == 1 and msgs[0].autor_tipo == "morador"
        an = db.scalar(select(OcorrenciaAnexo).where(OcorrenciaAnexo.mensagem_id == msgs[0].id)); assert an and (Path(UPLOAD_DIR) / an.arquivo).is_file() and str(an.id)[14] == "7"
    time.sleep(0.3)
    dest = {p for p, _, _ in enviados}; assert dest == {mail.MAIL_CONTATO, "ana@example.com"}, dest
    assert all(f"Ocorrência nº {o.numero}" in a and "foto.png" in c for _, a, c in enviados)
    assert "Cópia do registro" in [c for p, _, c in enviados if p == "ana@example.com"][0]
    # anexo servido só ao apto dono e ao admin
    assert mc.get(f"/morador/ocorrencias/anexo/{an.id}").status_code == 200 and ac.get(f"/admin/ocorrencias/anexo/{an.id}").status_code == 200
    # imutável: não existem rotas de edição/exclusão
    assert mc.post(f"/morador/ocorrencias/{oid}/excluir").status_code in (404, 405) and ac.post(f"/admin/ocorrencias/{oid}/excluir").status_code in (404, 405)
    # anexo inválido é recusado sem gravar
    assert "apenas PDF" in unquote(mc.post("/morador/ocorrencias", data={"titulo": TIT, "texto": "x", **captcha()}, files=[("arquivos", ("v.exe", b"1", "application/octet-stream"))], follow_redirects=False).headers["location"])
    assert "Arquivo recusado" in unquote(mc.post("/morador/ocorrencias", data={"titulo": TIT, "texto": "x", **captcha()}, files=[("arquivos", ("v.png", b"MZ\x90\x00 nao e png", "image/png"))], follow_redirects=False).headers["location"])

    # admin vê "aguardando resposta", responde com anexo -> e-mail ao condômino e aviso na área
    lst = ac.get("/admin/ocorrencias").text; assert "aguardando resposta" in lst and "Registrada por · quando · IP" in lst and "Última interação" in lst and "<strong>Ana Oc</strong>" in lst
    assert "Ocorrências (1)" in ac.get("/admin").text
    assert "Declaração de responsabilidade aceita" in ac.get(f"/admin/ocorrencias/{oid}").text  # abrir marca como vista
    assert "Ocorrências (1)" not in ac.get("/admin").text
    enviados.clear()
    r = ac.post(f"/admin/ocorrencias/{oid}/mensagem", data={"texto": "Zelador trocará amanhã."}, files=[("arquivos", ("os.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert r.status_code == 303, r.headers; time.sleep(0.5)
    assert [p for p, _, _ in enviados] == ["ana@example.com"] and "resposta da administração" in enviados[0][1] and "Zelador trocará" in enviados[0][2] and "os.pdf" in enviados[0][2]
    assert "Ocorrências (1)" in mc.get("/morador").text and "resposta nova" in mc.get("/morador/ocorrencias").text and "1 ocorrência(s) com resposta nova" in mc.get("/morador").text
    pg = mc.get(f"/morador/ocorrencias/{oid}").text; assert "Zelador trocará" in pg and "os.pdf" in pg and "Administração ·" in pg
    assert "Ocorrências (1)" not in mc.get("/morador").text  # abrir zera o aviso
    assert "aguardando resposta" not in ac.get("/admin/ocorrencias").text

    # condômino responde -> e-mail a contato@; finaliza -> ninguém mais escreve
    enviados.clear()
    assert mc.post(f"/morador/ocorrencias/{oid}/mensagem", data={"texto": "Obrigado."}, follow_redirects=False).status_code == 303; time.sleep(0.3)
    assert [p for p, _, _ in enviados] == [mail.MAIL_CONTATO]
    assert mc.post(f"/morador/ocorrencias/{oid}/finalizar", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        o = db.get(Ocorrencia, __import__("uuid").UUID(oid)); assert o.status == "finalizada" and o.finalizada_em and o.finalizada_ip
    assert mc.post(f"/morador/ocorrencias/{oid}/mensagem", data={"texto": "mais"}).status_code == 400
    assert ac.post(f"/admin/ocorrencias/{oid}/mensagem", data={"texto": "mais"}).status_code == 400
    assert "Finalizada" in mc.get(f"/morador/ocorrencias/{oid}").text and TIT in ac.get("/admin/ocorrencias?status=finalizada").text
    with SessionLocal() as db:
        assert db.scalar(select(Ocorrencia).where(Ocorrencia.id == o.id)) is not None and len(db.scalars(select(OcorrenciaMensagem).where(OcorrenciaMensagem.ocorrencia_id == o.id)).all()) == 3
    print("check_ocorrencias ok")
finally:
    limpar()
