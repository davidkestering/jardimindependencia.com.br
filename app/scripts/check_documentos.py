"""Upload múltiplo de documentos com limite total e vínculo a assembleia. Limpa o que cria:
docker exec condominio-app python scripts/check_documentos.py"""
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True

from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
import routers.admin as adm
from models import AdminUser, Assembleia, Documento

TIT = "Assembleia doc teste"


def limpar():
    with SessionLocal() as db:
        for d in db.scalars(select(Documento).where(Documento.nome_original.like("teste-%"))):
            (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True); db.delete(d)
        db.execute(delete(Assembleia).where(Assembleia.titulo == TIT))
        from models import CategoriaDocumento
        db.execute(delete(CategoriaDocumento).where(CategoriaDocumento.nome.ilike("laudos teste"))); db.commit()


limpar()
try:
    with SessionLocal() as db:
        a = db.scalar(select(AdminUser).where(AdminUser.master))
        agora = datetime.now(timezone.utc)
        db.add(Assembleia(titulo=TIT, abre_em=agora - timedelta(days=1), fecha_em=agora + timedelta(days=1))); db.commit()
        asm = db.scalar(select(Assembleia).where(Assembleia.titulo == TIT))
    ac = TestClient(app, base_url="https://t"); ac.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(a.id)))
    pdf = b"%PDF-1.4 " + b"x" * 3000

    # 3 arquivos num envio, 2 vinculados à assembleia (título vazio usa o nome do arquivo)
    r = ac.post("/admin/documentos", data={"categoria": "Atas de assembleia", "assembleia_id": str(asm.id), "publico": "1"},
                files=[("arquivos", ("teste-ata.pdf", pdf, "application/pdf")), ("arquivos", ("teste-lista.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert r.status_code == 303 and "erro" not in r.headers["location"], r.headers
    r = ac.post("/admin/documentos", data={"categoria": "Outros", "titulo": "Balancete"}, files=[("arquivos", ("teste-b.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png"))], follow_redirects=False)
    assert r.status_code == 303 and "erro" not in r.headers["location"]
    with SessionLocal() as db:
        docs = db.scalars(select(Documento).where(Documento.nome_original.like("teste-%"))).all()
        assert len(docs) == 3
        por_nome = {d.nome_original: d for d in docs}
        assert por_nome["teste-ata.pdf"].assembleia_id == asm.id and por_nome["teste-ata.pdf"].titulo == "teste-ata"
        assert por_nome["teste-b.png"].assembleia_id is None and por_nome["teste-b.png"].titulo == "Balancete"
        assert all((Path(UPLOAD_DIR) / d.arquivo).stat().st_size > 0 for d in docs)
        assert all(d.enviado_por == a.login and d.enviado_ip for d in docs)
        import re
        for d in docs:  # arquivo = <uuid do registro>_ddmmyyyy_hhmmss.ext
            assert re.fullmatch(rf"documentos/{d.id}_\d{{8}}_\d{{6}}\.(pdf|png)", d.arquivo), d.arquivo
            assert str(d.id)[14] == "7"  # UUID v7
    pg = ac.get(f"/admin/assembleias/{asm.id}").text; assert "Documentos da assembleia" in pg and "teste-ata" in pg and "teste-lista" in pg and 'name="assembleia_id" value="' + str(asm.id) in pg
    r = ac.post("/admin/documentos", data={"categoria": "Outros", "assembleia_id": str(asm.id), "voltar": f"/admin/assembleias/{asm.id}"}, files=[("arquivos", ("teste-viaasm.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert r.headers["location"] == f"/admin/assembleias/{asm.id}" and "teste-viaasm" in ac.get(f"/admin/assembleias/{asm.id}").text
    assert "Avulso" in ac.get("/admin/documentos").text
    pg = ac.get("/admin/documentos").text; assert TIT in pg and 'multiple' in pg and "dlg-cat" in pg and f"<strong>{a.login}</strong>" in pg and "IP " in pg
    import re
    for html in (pg, ac.get(f"/admin/assembleias/{asm.id}").text):  # nenhum <form> aberto dentro de outro (quebra o envio no navegador)
        prof = 0
        for tag in re.findall(r"<form\b|</form>", html):
            prof += 1 if tag.startswith("<form") else -1
            assert prof in (0, 1), "form aninhado"
    # nova categoria via modal (dedup sem diferenciar maiúsculas), disponível nas duas telas
    from models import CategoriaDocumento
    with SessionLocal() as db: db.execute(delete(CategoriaDocumento).where(CategoriaDocumento.nome.ilike("laudos teste"))); db.commit()
    assert ac.post("/admin/categorias", data={"nome": "  Laudos   teste ", "voltar": f"/admin/assembleias/{asm.id}"}, follow_redirects=False).headers["location"] == f"/admin/assembleias/{asm.id}"
    ac.post("/admin/categorias", data={"nome": "laudos TESTE"})
    with SessionLocal() as db: assert db.scalar(select(func.count()).select_from(CategoriaDocumento).where(CategoriaDocumento.nome.ilike("laudos teste"))) == 1
    assert "Laudos teste" in ac.get("/admin/documentos").text and "Laudos teste" in ac.get(f"/admin/assembleias/{asm.id}").text
    r = ac.post("/admin/documentos", data={"categoria": "Laudos teste"}, files=[("arquivos", ("teste-cat.pdf", pdf, "application/pdf"))], follow_redirects=False)
    with SessionLocal() as db: assert db.scalar(select(Documento).where(Documento.nome_original == "teste-cat.pdf")).categoria == "Laudos teste"

    # extensão inválida e assembleia inexistente
    assert "erro=Envie" in ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", ("teste-x.exe", b"1", "application/octet-stream"))], follow_redirects=False).headers["location"]
    assert "erro=Assembleia" in ac.post("/admin/documentos", data={"categoria": "Outros", "assembleia_id": "nao-uuid"}, files=[("arquivos", ("teste-y.pdf", pdf, "application/pdf"))], follow_redirects=False).headers["location"]

    # conteúdo: executável disfarçado de PNG e PDF com JavaScript são recusados sem deixar arquivo
    from urllib.parse import unquote
    for nome, dados in (("teste-falso.png", b"MZ\x90\x00" + b"0" * 100), ("teste-js.pdf", b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj"),
                        ("teste-exe.pdf", b"#!/bin/sh\necho x")):
        r = ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", (nome, dados, "application/octet-stream"))], follow_redirects=False)
        assert "Arquivo recusado" in unquote(r.headers["location"]), (nome, r.headers["location"])
    with SessionLocal() as db:
        assert db.scalar(select(Documento).where(Documento.nome_original.in_(["teste-falso.png", "teste-js.pdf", "teste-exe.pdf"]))) is None
    assert not [p for p in Path(UPLOAD_DIR, "documentos").glob("*") if p.stat().st_size in (104, 89, 17)]
    # antivírus: EICAR é recusado (função direta e via upload em PDF)
    from antivirus import escanear
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    tmp = Path(UPLOAD_DIR, "documentos", "eicar-check.tmp"); tmp.write_bytes(eicar); limpo, det = escanear(tmp); tmp.unlink()
    assert limpo is False and "eicar" in det.lower(), det
    r = ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", ("teste-eicar.pdf", b"%PDF-1.4\n" + eicar, "application/pdf"))], follow_redirects=False)
    assert "recusado" in unquote(r.headers["location"]), r.headers["location"]
    # limite total: com MAX_TOTAL_MB=1, dois arquivos de 700 KB estouram; nada fica gravado
    adm.MAX_TOTAL_MB = 1
    grande = b"%PDF" + b"x" * (700 * 1024)
    r = ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", ("teste-g1.pdf", grande, "application/pdf")), ("arquivos", ("teste-g2.pdf", grande, "application/pdf"))], follow_redirects=False)
    from urllib.parse import unquote
    assert "passou de 1 MB" in unquote(r.headers["location"]), r.headers["location"]
    with SessionLocal() as db:
        assert db.scalar(select(Documento).where(Documento.nome_original.like("teste-g%"))) is None
    print("check_documentos ok")
finally:
    adm.MAX_TOTAL_MB = 100
    limpar()
