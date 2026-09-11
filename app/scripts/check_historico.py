"""Auditoria e exclusão lógica: registrar grava em historico (quem/IP/quando); documento, assembleia e pauta nunca
são apagados. Limpa o que cria: docker exec condominio-app python scripts/check_historico.py"""
import sys, time
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True

from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
from models import AdminUser, Assembleia, Documento, Historico, Pauta, Voto

TIT = "Assembleia hist teste"


def limpar():
    with SessionLocal() as db:
        for d in db.scalars(select(Documento).where(Documento.nome_original.like("hist-%"))):
            (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True); db.delete(d)
        for a in db.scalars(select(Assembleia).where(Assembleia.titulo == TIT)):
            for p in db.scalars(select(Pauta).where(Pauta.assembleia_id == a.id)):
                db.execute(delete(Voto).where(Voto.pauta_id == p.id)); db.delete(p)
            db.delete(a)
        db.execute(delete(Historico).where(Historico.detalhe.cast(__import__("sqlalchemy").String).ilike("%hist teste%") | Historico.detalhe.cast(__import__("sqlalchemy").String).ilike("%hist-doc%")))
        db.commit()


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
    ac = TestClient(app, base_url="https://t"); ac.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(adm.id)))
    ac.headers["X-Real-IP"] = "203.0.113.9"

    # documento: publicar, tornar privado, excluir -> arquivo e registro ficam; histórico com quem/IP
    ac.post("/admin/documentos", data={"categoria": "Outros", "titulo": "hist-doc", "publico": "1"}, files=[("arquivos", ("hist-a.pdf", b"%PDF hist", "application/pdf"))])
    with SessionLocal() as db:
        d = db.scalar(select(Documento).where(Documento.nome_original == "hist-a.pdf")); did = d.id; caminho = Path(UPLOAD_DIR) / d.arquivo
    pub = TestClient(app, base_url="https://t")
    ac.post(f"/admin/documentos/{did}/publico", data={"publico": "0"})
    ac.post(f"/admin/documentos/{did}/excluir")
    with SessionLocal() as db:
        d = db.get(Documento, did); assert d and d.excluido_em and d.excluido_por == adm.login and d.excluido_ip == "203.0.113.9" and not d.publico
    assert caminho.is_file()  # nunca apaga o arquivo
    assert ac.get(f"/admin/documentos/{did}").status_code == 200  # administração ainda baixa
    pg = ac.get("/admin/documentos").text; assert "documento(s) excluído(s)" in pg and "hist-doc" in pg
    time.sleep(0.3)
    with SessionLocal() as db:
        acoes = [h.acao for h in db.scalars(select(Historico).where(Historico.login == adm.login, Historico.ip == "203.0.113.9").order_by(Historico.quando))]
        assert "Documentos enviados" in acoes and "Documento tornado privado" in acoes and "Documento excluído (lógico)" in acoes, acoes
        h = db.scalar(select(Historico).where(Historico.acao == "Documento excluído (lógico)").order_by(Historico.quando.desc()))
        assert h.detalhe["titulo"] == "hist-doc" and h.tipo == "admin"

    # assembleia + pauta: exclusão lógica mantém votos
    agora = datetime.now(timezone.utc)
    r = ac.post("/admin/assembleias", data={"titulo": TIT, "abre_em": (agora - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"), "fecha_em": (agora + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")}, follow_redirects=False)
    aid = r.headers["location"].rsplit("/", 1)[1]
    ac.post(f"/admin/assembleias/{aid}/pautas", data={"texto": "Pauta hist teste", "opcoes": "Sim\nNão"})
    with SessionLocal() as db:
        p = db.scalar(select(Pauta).where(Pauta.assembleia_id == aid)); pid = p.id
    ac.post(f"/admin/assembleias/{aid}/pautas/{pid}/excluir")
    with SessionLocal() as db:
        p = db.get(Pauta, pid); assert p and p.excluido_em and p.excluido_por == adm.login
        assert db.get(Assembleia, aid).pautas == []  # some da assembleia, fica no banco
    ac.post(f"/admin/assembleias/{aid}/excluir")
    with SessionLocal() as db:
        a = db.get(Assembleia, aid); assert a and a.excluido_em and a.excluido_ip == "203.0.113.9"
    assert "assembleia(s) excluída(s)" in ac.get("/admin/assembleias").text and "Assembleia excluída por" in ac.get(f"/admin/assembleias/{aid}").text

    # histórico visível só ao mestre, com busca
    hp = ac.get("/admin/historico?q=hist-doc").text; assert "Documento excluído (lógico)" in hp and "203.0.113.9" in hp
    print("check_historico ok")
finally:
    limpar()
