"""Checagem dos comunicados (rascunho/condôminos/público, notificação, badge). Roda no container e limpa o que cria:
docker exec condominio-app python scripts/check_comunicados.py"""
import io, re, sys, time, zipfile
from pathlib import Path
from urllib.parse import unquote
sys.path.insert(0, "/app")
import mail, interfone, apns
enviados, pushes = [], []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True  # ignora e-mails de log
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria
interfone.push_para_todos = lambda payload, ttl=0: pushes.append(payload)
apns.notificar = lambda *a, **k: None

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from config import UPLOAD_DIR
from main import app
from models import AdminUser, Comunicado, Documento, Morador
from termo import TERMO
from unidades_teste import unidades

CPF = "52998224725"
TIT = "Comunicado de teste automático"
TEXTO = ("Manutenção da caixa de água. " * 20).strip()  # > 200 chars
PDF = b"%PDF-1.4 " + b"x" * 3000


class Virus:
    """Simula detecção do ClamAV no caminho do upload (o EICAR real só é reconhecido sem prefixo de PDF)."""
    def __enter__(self):
        import routers.admin as ra
        self.ra, self.orig = ra, ra.escanear; ra.escanear = lambda caminho: (False, "Teste-Malware FOUND")
    def __exit__(self, *a):
        self.ra.escanear = self.orig


def docx(extra: dict | None = None, corpo: bytes = b"<w:p><w:r><w:t>Ola</w:t></w:r></w:p>") -> bytes:
    """Word mínimo (ZIP Office). `extra` acrescenta membros (macro, OLE...)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", b'<?xml version="1.0"?><w:document><w:body>' + corpo + b"</w:body></w:document>")
        for n, c in (extra or {}).items():
            z.writestr(n, c)
    return buf.getvalue()


def arquivos_teste():
    return list(Path(UPLOAD_DIR, "documentos").glob("*"))


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Comunicado).where(Comunicado.titulo.like(TIT + "%")))
        for d in db.scalars(select(Documento).where(Documento.categoria == "Comunicados", Documento.titulo.like(TIT + "%"))):
            Path(UPLOAD_DIR, d.arquivo).unlink(missing_ok=True); db.delete(d)
        db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master))
        u1, u2 = unidades(db, 2)
        for u in (u1, u2):  # mesmo CPF/e-mail em 2 aptos: deve receber 1 e-mail
            db.add(Morador(unidade_id=u.id, nome="Ana Teste", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        db.commit()
        m1 = db.scalar(select(Morador).where(Morador.cpf == CPF, Morador.unidade_id == u1.id))
    ac, mc, pub = cliente("admin", adm.id), cliente("morador", m1.id), TestClient(app, base_url="https://t")

    # rascunho: só admin vê
    assert ac.post("/admin/comunicados", data={"titulo": TIT, "texto": TEXTO, "visibilidade": "rascunho"}, follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        c = db.scalar(select(Comunicado).where(Comunicado.titulo == TIT)); cid = c.id; assert c.publicado_em is None
        assert c.autor == adm.login and c.criado_ip
    assert TIT in ac.get("/admin/comunicados").text and f"/admin/comunicados/{cid}/preview" in ac.get("/admin/comunicados").text
    pv = ac.get(f"/admin/comunicados/{cid}/preview").text
    assert TEXTO in pv and "Pré-visualização" in pv and "Publicar: só condôminos" in pv and "Publicar: público" in pv and "Voltar a rascunho" not in pv
    assert TIT not in pub.get("/comunicados").text and TIT not in mc.get("/morador/comunicados").text
    assert pub.get(f"/comunicados/{cid}").status_code == 404 and mc.get(f"/morador/comunicados/{cid}").status_code == 404
    assert not enviados and not pushes

    # só condôminos: notifica 1x, aparece na área e no painel como novo, não no site
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "condominos"}); time.sleep(0.5)
    dest = [e[0] for e in enviados]; assert dest.count("ana@example.com") == 1 and len(dest) == len(set(dest)), dest  # 1 por endereço (2 aptos)
    with SessionLocal() as db:
        c = db.get(Comunicado, cid); assert c.publicado_por == adm.login and c.publicado_ip
        from routers.comunicados import por_mes
        MES = por_mes([c])[0][0]  # título do grupo na lista: mês e ano da publicação
    assert "Publicado por" in ac.get("/admin/comunicados").text
    assert TIT in enviados[0][1] and f"/morador/comunicados/{cid}" in enviados[0][2]
    assert pushes and pushes[0]["tag"] == "comunicado" and pushes[0]["url"].endswith(str(cid))
    painel = mc.get("/morador").text; assert "1 comunicado(s) novo(s)" in painel and "Comunicados (1)" in painel
    lst = mc.get("/morador/comunicados").text; assert TIT in lst and "· novo" in lst and MES in lst and "…" in lst and TEXTO not in lst
    assert "Comunicados (1)" not in mc.get("/morador").text  # badge zerado após abrir a lista
    assert TEXTO in mc.get(f"/morador/comunicados/{cid}").text
    # histórico: quem abriu a página de comunicados e quem leu cada comunicado (só histórico, sem e-mail a logs@)
    _h = mail._gravar_historico; lidos = []; mail._gravar_historico = lambda tipo, login, ip, acao, dados: lidos.append((tipo, acao, dados.get("comunicado")))
    mc.get("/morador/comunicados"); mc.get(f"/morador/comunicados/{cid}"); mail._gravar_historico = _h
    assert lidos == [("morador", "Comunicados: página acessada", None), ("morador", "Comunicado lido", TIT)], lidos
    assert TIT not in pub.get("/comunicados").text and TIT not in pub.get("/").text

    # público: home (antes dos cards de moradores), lista por mês, detalhe; sem reenvio
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "publico"}); time.sleep(0.3)
    assert len(enviados) == len(dest) and len(pushes) == 1  # nenhum reenvio ao mudar de nível
    home = pub.get("/").text; assert TIT in home and home.index("Comunicados públicos") < home.index("Para moradores") and TEXTO not in home
    lst = pub.get("/comunicados").text; assert TIT in lst and MES in lst
    assert TEXTO in pub.get(f"/comunicados/{cid}").text

    # edição e volta a rascunho
    ac.post(f"/admin/comunicados/{cid}", data={"titulo": TIT + " 2", "texto": "novo texto"})
    assert TIT + " 2" in pub.get(f"/comunicados/{cid}").text
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "rascunho"})
    assert pub.get(f"/comunicados/{cid}").status_code == 404 and TIT not in pub.get("/").text
    assert ac.post("/admin/comunicados", data={"titulo": "", "texto": "x"}).status_code == 400
    ac.post(f"/admin/comunicados/{cid}/visibilidade", data={"visibilidade": "publico"}); time.sleep(0.3)
    assert len(pushes) == 2 and len(enviados) == 2 * len(dest)  # saiu de rascunho de novo: avisa de novo
    ac.post(f"/admin/comunicados/{cid}/excluir")
    with SessionLocal() as db:
        c = db.get(Comunicado, cid); assert c and c.excluido_em and c.excluido_por == adm.login and c.excluido_ip  # nunca apaga
    assert pub.get(f"/comunicados/{cid}").status_code == 404 and mc.get(f"/morador/comunicados/{cid}").status_code == 404
    assert TIT not in pub.get("/comunicados").text and TIT + " 2" not in mc.get("/morador/comunicados").text.split("Histórico")[0]
    lst = ac.get("/admin/comunicados").text; assert "comunicado(s) excluído(s)" in lst and TIT + " 2" in lst
    assert "Excluído por" in ac.get(f"/admin/comunicados/{cid}/preview").text

    # ---- comunicado com documento (PDF ou Word .docx) ----
    TD = TIT + " doc"
    def criar(nome, conteudo, vis="rascunho"):
        r = ac.post("/admin/comunicados", data={"titulo": TD, "texto": "Resumo do documento.", "visibilidade": vis},
                    files={"arquivo": (nome, conteudo, "application/octet-stream")}, follow_redirects=False)
        return unquote(r.headers["location"])
    antes = len(arquivos_teste())
    # recusas: extensão, assinatura interna, Word com macro, Word com OLE embutido, Word com DDE, PDF com JavaScript, vírus (EICAR)
    assert "PDF ou Word" in criar("x.doc", PDF)
    assert "PDF ou Word" in criar("x.docm", docx())
    assert "não corresponde" in criar("x.pdf", b"MZ" + b"x" * 100)
    assert "não corresponde" in criar("x.docx", PDF)
    assert "não corresponde" in criar("macro.docx", docx({"word/vbaProject.bin": b"\xd0\xcf\x11\xe0"}))
    assert "não corresponde" in criar("ole.docx", docx({"word/embeddings/oleObject1.bin": b"\xd0\xcf\x11\xe0"}))
    assert "não corresponde" in criar("dde.docx", docx(corpo=b'<w:p><w:r><w:instrText> DDEAUTO c:\\windows\\system32\\cmd.exe </w:instrText></w:r></w:p>'))
    assert "não corresponde" in criar("js.pdf", b"%PDF-1.4 /OpenAction << /S /JavaScript >>")
    with Virus():
        assert "recusado pelo antivírus" in criar("virus.pdf", PDF)
    assert "não corresponde" in criar("bomba.docx", docx({"word/media/x.bin": b"\0" * (101 * 1024 * 1024)}))
    assert "não corresponde" in criar("ole2.docx", docx({"Word/Embeddings/x.bin": b"x"}))  # nome em maiúsculas
    assert "não corresponde" in criar("dde-rodape.docx", docx({"word/footer1.xml": b"<w:r><w:t>DDE</w:t></w:r><w:r><w:t>AUTO cmd</w:t></w:r>"}))  # partido em runs
    assert "não corresponde" in criar("modelo.docx", docx({"word/_rels/settings.xml.rels": b'<Relationships><Relationship Type="x/attachedTemplate" Target="http://x/m.dotm" TargetMode="External"/></Relationships>'}))
    dup = io.BytesIO()
    with zipfile.ZipFile(dup, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>"); z.writestr("word/document.xml", "<w:instrText> DDEAUTO x </w:instrText>"); z.writestr("word/document.xml", "<w:document/>")
    assert "não corresponde" in criar("dup.docx", dup.getvalue())
    assert len(arquivos_teste()) == antes and not db.scalar(select(Comunicado).where(Comunicado.titulo == TD))  # nada gravado
    # aceito: Word limpo, em rascunho
    DOCX = docx()  # ZIP leva a hora de criação: gerar uma vez para comparar o download byte a byte
    assert "Comunicado salvo" in criar("link.docx", docx({"word/_rels/document.xml.rels": b'<Relationships><Relationship Type="x/hyperlink" Target="https://x" TargetMode="External"/></Relationships>'}))  # hiperlink é normal
    with SessionLocal() as db:
        lk = db.scalar(select(Comunicado).where(Comunicado.titulo == TD)); Path(UPLOAD_DIR, lk.documento.arquivo).unlink(); db.delete(lk.documento); db.delete(lk); db.commit()
    assert "Comunicado salvo com o documento edital.docx" in criar("edital.docx", DOCX)
    with SessionLocal() as db:
        c = db.scalar(select(Comunicado).where(Comunicado.titulo == TD)); did = c.id; d = c.documento
        assert re.fullmatch(rf"documentos/{d.id}_\d{{8}}_\d{{6}}\.docx", d.arquivo) and d.nome_original == "edital.docx" and Path(UPLOAD_DIR, d.arquivo).is_file()
        # é um Documento da categoria Comunicados, com o título do comunicado; privado enquanto rascunho
        assert d.categoria == "Comunicados" and d.titulo == TD and d.publico is False and d.enviado_por == adm.login and d.excluido_em is None
        doc1 = d.id
    assert TD not in mc.get("/morador/documentos?categoria=Comunicados").text
    assert "edital.docx" in ac.get("/admin/comunicados").text and f"/admin/comunicados/{did}/documento" in ac.get(f"/admin/comunicados/{did}/preview").text
    r = ac.get(f"/admin/comunicados/{did}/documento"); assert r.status_code == 200 and r.content == DOCX and "edital.docx" in r.headers["content-disposition"]
    assert mc.get(f"/morador/comunicados/{did}/documento").status_code == 404 and pub.get(f"/comunicados/{did}/documento").status_code == 404  # rascunho
    # só condôminos: condômino baixa, site não; e-mail traz o link do documento
    n_env = len(enviados); ac.post(f"/admin/comunicados/{did}/visibilidade", data={"visibilidade": "condominos"}); time.sleep(0.5)
    assert f"/morador/comunicados/{did}/documento" in enviados[n_env][2]
    docs = mc.get("/morador/documentos?categoria=Comunicados").text; assert TD in docs and "edital.docx" in docs and f"/morador/documentos/{doc1}" in docs
    assert mc.get(f"/morador/documentos/{doc1}").content == DOCX  # pela tela de Documentos também
    pg = mc.get(f"/morador/comunicados/{did}").text; assert "Baixar documento: edital.docx" in pg and f"/morador/comunicados/{did}/documento" in pg
    assert "📎 edital.docx" in mc.get("/morador/comunicados").text
    assert mc.get(f"/morador/comunicados/{did}/documento").content == DOCX and pub.get(f"/comunicados/{did}/documento").status_code == 404
    _h = mail._gravar_historico; baixados = []; mail._gravar_historico = lambda tipo, login, ip, acao, dados: baixados.append((acao, dados.get("arquivo")))
    mc.get(f"/morador/comunicados/{did}/documento"); mail._gravar_historico = _h
    assert baixados == [("Comunicado: documento baixado", "edital.docx")], baixados
    # público: site baixa
    ac.post(f"/admin/comunicados/{did}/visibilidade", data={"visibilidade": "publico"})
    assert pub.get(f"/comunicados/{did}/documento").content == DOCX and "📎 edital.docx" in pub.get("/comunicados").text
    # edição: substituir por PDF (o Documento anterior fica excluído, arquivo preservado), recusa inválido sem perder o atual, remover
    antigo = d.arquivo
    r = ac.post(f"/admin/comunicados/{did}", data={"titulo": TD, "texto": "Resumo novo"}, files={"arquivo": ("edital.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert "Alterações salvas" in unquote(r.headers["location"])
    with SessionLocal() as db:
        d = db.get(Comunicado, did).documento; assert d.id != doc1 and d.arquivo.endswith(".pdf") and d.nome_original == "edital.pdf" and d.publico is True and d.titulo == TD
        d1 = db.get(Documento, doc1); assert d1.excluido_em and "Substituído" in d1.excluido_motivo and Path(UPLOAD_DIR, antigo).exists()
    docs = mc.get("/morador/documentos?categoria=Comunicados").text; assert "edital.pdf" in docs and "edital.docx" not in docs
    assert pub.get(f"/comunicados/{did}/documento").content == PDF
    with Virus():
        r = ac.post(f"/admin/comunicados/{did}", data={"titulo": TD, "texto": "Resumo x"}, files={"arquivo": ("virus.pdf", PDF, "application/pdf")}, follow_redirects=False)
    assert "recusado pelo antivírus" in unquote(r.headers["location"])
    with SessionLocal() as db:
        c2 = db.get(Comunicado, did); d2 = c2.documento; assert d2.nome_original == "edital.pdf" and c2.texto == "Resumo novo" and Path(UPLOAD_DIR, d2.arquivo).is_file()
        doc2 = d2.id
    # título editado reflete no Documento
    ac.post(f"/admin/comunicados/{did}", data={"titulo": TD + " novo", "texto": "Resumo novo"})
    with SessionLocal() as db: assert db.get(Documento, doc2).titulo == TD + " novo"
    ac.post(f"/admin/comunicados/{did}", data={"titulo": TD, "texto": "Resumo sem doc", "remover_documento": "1"})
    with SessionLocal() as db:
        c3 = db.get(Comunicado, did); assert c3.documento_id is None
        d2 = db.get(Documento, doc2); assert d2.excluido_em and "Removido" in d2.excluido_motivo and Path(UPLOAD_DIR, d2.arquivo).is_file()
    assert pub.get(f"/comunicados/{did}/documento").status_code == 404 and "Baixar documento" not in pub.get(f"/comunicados/{did}").text
    assert TD not in mc.get("/morador/documentos?categoria=Comunicados").text
    # excluir o comunicado exclui o documento junto; voltar a rascunho torna o documento privado
    assert "Comunicado salvo" in criar("final.pdf", PDF, "publico")
    with SessionLocal() as db:
        c4 = db.scalar(select(Comunicado).where(Comunicado.titulo == TD, Comunicado.excluido_em.is_(None), Comunicado.documento_id.is_not(None))); cid4, doc4 = c4.id, c4.documento_id
        assert c4.documento.publico is True
    ac.post(f"/admin/comunicados/{cid4}/visibilidade", data={"visibilidade": "rascunho"})
    with SessionLocal() as db: assert db.get(Documento, doc4).publico is False
    ac.post(f"/admin/comunicados/{cid4}/excluir")
    with SessionLocal() as db:
        d4 = db.get(Documento, doc4); assert d4.excluido_em and d4.excluido_motivo == "Comunicado excluído" and d4.excluido_por == adm.login
    assert len(arquivos_teste()) == antes + 3  # edital.docx, edital.pdf e final.pdf ficam no disco (exclusão lógica)
    print("check_comunicados ok")
finally:
    limpar()
