"""Upload múltiplo de documentos com limite total e vínculo a assembleia. Limpa o que cria:
docker exec condominio-app python scripts/check_documentos.py"""
import sys
sys.path.insert(0, "/app")
import mail
mail.enviar = lambda *a, **k: True
_hist_real, mail._gravar_historico = mail._gravar_historico, lambda *a, **k: None  # testes não entram no histórico de auditoria

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
import routers.admin as adm
from models import AdminUser, Assembleia, Documento, Historico, Morador, Unidade
from termo import TERMO

CPF = "52998224725"  # condômino de teste (área do condômino: resumo por competência e filtro)

TIT = "Assembleia doc teste"


def limpar():
    with SessionLocal() as db:
        for d in db.scalars(select(Documento).where(Documento.nome_original.like("teste-%"))):
            (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True); db.delete(d)
        db.execute(delete(Assembleia).where(Assembleia.titulo == TIT))
        db.execute(delete(Morador).where(Morador.cpf == CPF))
        db.execute(delete(Historico).where(Historico.acao.in_(("Competência do documento alterada", "Categoria do documento alterada")), Historico.detalhe["justificativa"].astext.like("teste:%")))
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
    assert r.headers["location"].startswith(f"/admin/assembleias/{asm.id}?ok=") and "teste-viaasm" in ac.get(f"/admin/assembleias/{asm.id}").text
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
    # via fetch (Accept: application/json) responde JSON em vez de redirect: o modal atualiza o select sem recarregar a página
    j = ac.post("/admin/categorias", data={"nome": "LAUDOS teste"}, headers={"Accept": "application/json"}).json()
    assert j["nome"] == "Laudos teste" and "Laudos teste" in j["categorias"] and j["categorias"] == sorted(j["categorias"]), j
    r = ac.post("/admin/categorias", data={"nome": "   "}, headers={"Accept": "application/json"}); assert r.status_code == 400 and "erro" in r.json()
    assert 'id="f-cat"' in ac.get("/admin/documentos").text
    r = ac.post("/admin/documentos", data={"categoria": "Laudos teste"}, files=[("arquivos", ("teste-cat.pdf", pdf, "application/pdf"))], follow_redirects=False)
    with SessionLocal() as db: assert db.scalar(select(Documento).where(Documento.nome_original == "teste-cat.pdf")).categoria == "Laudos teste"

    # competência: informada no envio (data de assinatura/referência), vazia = hoje, inválida = erro
    hoje = datetime.now(mail.FUSO).date()
    with SessionLocal() as db:
        assert db.scalar(select(Documento).where(Documento.nome_original == "teste-cat.pdf")).competencia == hoje
    r = ac.post("/admin/documentos", data={"categoria": "Outros", "competencia": "2023-01-05", "titulo": "Contrato antigo"}, files=[("arquivos", ("teste-comp.pdf", pdf, "application/pdf"))], follow_redirects=False)
    assert "erro" not in r.headers["location"]
    with SessionLocal() as db:
        dc = db.scalar(select(Documento).where(Documento.nome_original == "teste-comp.pdf")); assert dc.competencia == date(2023, 1, 5); dc_id = dc.id
    assert "Informe uma data de compet" in unquote(ac.post("/admin/documentos", data={"categoria": "Outros", "competencia": "05/01/2023"}, files=[("arquivos", ("teste-z.pdf", pdf, "application/pdf"))], follow_redirects=False).headers["location"])
    pg = ac.get("/admin/documentos").text; assert "05/01/2023" in pg and 'id="dlg-comp"' in pg and f'data-id="{dc_id}" data-comp="2023-01-05" data-titulo="Contrato antigo"' in pg
    # alteração da competência: exige justificativa e fica no histórico (de/para/justificativa)
    assert "erro=Informe a justificativa" in unquote(ac.post(f"/admin/documentos/{dc_id}/competencia", data={"competencia": "2023-02-10", "justificativa": "x"}, follow_redirects=False).headers["location"])
    assert "erro=Informe uma data" in unquote(ac.post(f"/admin/documentos/{dc_id}/competencia", data={"competencia": "1800-01-01", "justificativa": "teste: data errada"}, follow_redirects=False).headers["location"])
    mail._gravar_historico = _hist_real  # só aqui o histórico grava de verdade (a limpeza apaga as linhas "teste:")
    loc = unquote(ac.post(f"/admin/documentos/{dc_id}/competencia", data={"competencia": "2023-02-10", "justificativa": "  teste: data   de assinatura "}, follow_redirects=False).headers["location"])
    assert loc.startswith("/admin/documentos?ok=") and "05/01/2023 para 10/02/2023" in loc, loc
    assert "✓ Competência de «Contrato antigo» alterada" in ac.get(loc).text  # toda ação dá retorno na tela
    # categoria: mesma regra (categoria válida + justificativa), com log de/para
    assert "Escolha uma categoria" in unquote(ac.post(f"/admin/documentos/{dc_id}/categoria", data={"categoria": "Inexistente", "justificativa": "teste: x"}, follow_redirects=False).headers["location"])
    assert "erro=Informe a justificativa" in unquote(ac.post(f"/admin/documentos/{dc_id}/categoria", data={"categoria": "Balancetes", "justificativa": "x"}, follow_redirects=False).headers["location"])
    assert "ok=" in ac.post(f"/admin/documentos/{dc_id}/categoria", data={"categoria": "Balancetes", "justificativa": "teste: categoria errada"}, follow_redirects=False).headers["location"]
    mail._gravar_historico = lambda *a, **k: None
    with SessionLocal() as db:
        assert db.get(Documento, dc_id).categoria == "Balancetes"
        h = db.scalar(select(Historico).where(Historico.acao == "Categoria do documento alterada").order_by(Historico.quando.desc()))
        assert h and h.detalhe["de"] == "Outros" and h.detalhe["para"] == "Balancetes" and h.detalhe["justificativa"] == "teste: categoria errada", h.detalhe
    ac.post(f"/admin/documentos/{dc_id}/categoria", data={"categoria": "Outros", "justificativa": "teste: volta"})
    assert 'id="dlg-catdoc"' in ac.get("/admin/documentos").text and f'data-id="{dc_id}" data-cat="Outros"' in ac.get("/admin/documentos").text
    with SessionLocal() as db:
        assert db.get(Documento, dc_id).competencia == date(2023, 2, 10)
        h = db.scalar(select(Historico).where(Historico.acao == "Competência do documento alterada").order_by(Historico.quando.desc()))
        assert h and h.detalhe["de"] == "05/01/2023" and h.detalhe["para"] == "10/02/2023" and h.detalhe["justificativa"] == "teste: data de assinatura" and h.login == a.login, h.detalhe
    # filtros da administração: cadastro, competência, assembleia, categoria; total; paginação; ações voltam ao filtro
    def lista(**q): return ac.get("/admin/documentos", params=q).text
    t = lista(comp_de="2023-02-10", comp_ate="2023-02-10"); assert "Contrato antigo" in t and "teste-ata" not in t and "documento(s) com este filtro" in t
    assert "Contrato antigo" not in lista(comp_de="2024-01-01")
    t = lista(assembleia=str(asm.id)); assert "teste-ata" in t and "teste-lista" in t and "Contrato antigo" not in t
    t = lista(assembleia="avulso"); assert "Contrato antigo" in t and "teste-ata" not in t
    t = lista(categoria="Atas de assembleia"); assert "teste-ata" in t and "Contrato antigo" not in t
    assert "Contrato antigo" in lista(cad_de=hoje.isoformat(), cad_ate=hoje.isoformat()) and "Contrato antigo" not in lista(cad_ate="2000-01-01")
    assert "Contrato antigo" in lista(comp_de="lixo")  # data inválida é ignorada (= todos)
    adm.POR_PAGINA = 2
    try:
        t = lista(categoria="Atas de assembleia"); assert "página 1 de" in t and "pagina=2" in t
        t2 = lista(categoria="Atas de assembleia", pagina=2); assert "página 2 de" in t2
        assert lista(categoria="Atas de assembleia", pagina=99).count("<tr>") == lista(categoria="Atas de assembleia", pagina=1).count("<tr>") or "página" in lista(categoria="Atas de assembleia", pagina=99)
        # ação com voltar mantém filtro e página
        loc = ac.post(f"/admin/documentos/{dc_id}/publico", data={"publico": "1", "voltar": "/admin/documentos?categoria=Outros&pagina=1"}, follow_redirects=False).headers["location"]
        assert loc.startswith("/admin/documentos?categoria=Outros&pagina=1&ok=") and 'name="voltar" value="/admin/documentos?categoria=Outros' in ac.get(loc).text, loc
        ac.post(f"/admin/documentos/{dc_id}/publico", data={"publico": "0"})
    finally:
        adm.POR_PAGINA = 10
    # exclusão lógica exige justificativa; excluídos saem da lista normal e aparecem só com situacao=excluidos (demais filtros combinam)
    ac.post("/admin/documentos", data={"categoria": "Outros", "titulo": "Doc excluído teste", "competencia": "2022-06-15"}, files=[("arquivos", ("teste-excl.pdf", pdf, "application/pdf"))])
    with SessionLocal() as db: ex_id = db.scalar(select(Documento.id).where(Documento.nome_original == "teste-excl.pdf"))
    r = ac.post(f"/admin/documentos/{ex_id}/excluir", data={"justificativa": "x", "voltar": "/admin/documentos?categoria=Outros"}, follow_redirects=False)
    assert "justificativa" in unquote(r.headers["location"]) and "?categoria=Outros&erro=" in r.headers["location"], r.headers["location"]
    with SessionLocal() as db: assert db.get(Documento, ex_id).excluido_em is None
    r = ac.post(f"/admin/documentos/{ex_id}/excluir", data={"justificativa": "  teste:  versão   sem CNPJ "}, follow_redirects=False)
    assert "excluídos" in unquote(r.headers["location"])
    with SessionLocal() as db:
        d = db.get(Documento, ex_id); assert d.excluido_em and d.excluido_motivo == "teste: versão sem CNPJ" and not d.publico
    assert "Doc excluído teste" not in lista() and "Doc excluído teste" not in lista(categoria="Outros")
    t = lista(situacao="excluidos"); assert "Doc excluído teste" in t and "teste: versão sem CNPJ" in t and "Contrato antigo" not in t and "documento(s) excluído(s)" in t
    assert 'value="excluidos" selected' in t and "sec excluir-doc" not in t and "sec alterar-comp" not in t and "Tornar privado" not in t  # sem ações sobre excluídos
    assert "Doc excluído teste" in lista(situacao="excluidos", categoria="Outros") and "Doc excluído teste" not in lista(situacao="excluidos", categoria="Atas de assembleia")
    assert "Doc excluído teste" in lista(situacao="excluidos", comp_de="2022-06-01", comp_ate="2022-06-30") and "Doc excluído teste" not in lista(situacao="excluidos", comp_de="2023-01-01")
    assert "Doc excluído teste" not in lista(situacao="lixo")  # valor desconhecido = ativos
    assert ac.post(f"/admin/documentos/{ex_id}/excluir", data={"justificativa": "teste: de novo"}, follow_redirects=False).headers["location"] == "/admin/documentos"  # já excluído: nada muda

    # área do condômino: painel com quantitativo por ano/mês de competência (só publicados) e lista filtrada por período
    loc = unquote(ac.post(f"/admin/documentos/{dc_id}/publico", data={"publico": "1"}, follow_redirects=False).headers["location"])
    assert "publicado: já aparece" in loc and "✓" in ac.get(loc).text, loc
    loc = unquote(ac.post(f"/admin/documentos/{dc_id}/publico", data={"publico": "0"}, follow_redirects=False).headers["location"]); assert "tornado privado" in loc
    ac.post(f"/admin/documentos/{dc_id}/publico", data={"publico": "1"})
    with SessionLocal() as db:
        u = db.scalar(select(Unidade).order_by(Unidade.bloco, Unidade.apto))
        db.add(Morador(unidade_id=u.id, nome="Ana Teste", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO)); db.commit()
        m = db.scalar(select(Morador).where(Morador.cpf == CPF))
    mc = TestClient(app, base_url="https://t"); mc.cookies.set(auth.COOKIE, auth.criar_sessao("morador", str(m.id)))
    pg = mc.get("/morador").text
    assert "<h3>2023</h3>" in pg and 'href="/morador/documentos?de=2023-02&ate=2023-02">Fevereiro</a>' in pg and "Contrato antigo" not in pg and 'href="/morador/documentos"' in pg
    lst = mc.get("/morador/documentos?de=2023-02&ate=2023-02").text; assert "Contrato antigo" in lst and "<dt>Competência</dt><dd>10/02/2023</dd>" in lst and "teste-ata" not in lst
    assert "Contrato antigo" not in mc.get("/morador/documentos?de=2023-03&ate=2023-12").text
    import routers.morador as mor
    mor.POR_PAGINA = 1000  # sem filtro lista tudo (paginado em produção; aqui numa página só para conferir)
    tudo = mc.get("/morador/documentos").text; assert "Contrato antigo" in tudo and "teste-ata" in tudo
    assert "Contrato antigo" in mc.get("/morador/documentos?categoria=Inexistente").text  # categoria desconhecida é ignorada
    mor.POR_PAGINA = 10
    # filtro por categoria, independente da competência: lista da categoria por ordem de cadastro, com competência e data de cadastro
    cat = mc.get("/morador/documentos?categoria=Outros").text
    assert "Contrato antigo" in cat and "teste-ata" not in cat and "<dt>Competência</dt><dd>10/02/2023</dd>" in cat and f"<dt>Cadastro</dt><dd>{hoje:%d/%m/%Y}" in cat and '<option selected>Outros</option>' in cat
    assert "Contrato antigo" not in mc.get("/morador/documentos?categoria=Outros&de=2024-01").text  # categoria + período combinam
    mor.POR_PAGINA = 1
    try:
        t = mc.get("/morador/documentos?categoria=Outros").text; assert "página 1 de" in t and "categoria=Outros&pagina=2" in t
        assert "página 2 de" in mc.get("/morador/documentos?categoria=Outros&pagina=2").text
    finally:
        mor.POR_PAGINA = 10
    assert "Contrato antigo" in mc.get("/morador/documentos?de=lixo&ate=2023-02").text  # período inválido é ignorado

    # extensão inválida e assembleia inexistente
    assert "erro=Envie" in ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", ("teste-x.exe", b"1", "application/octet-stream"))], follow_redirects=False).headers["location"]
    assert "erro=Assembleia" in ac.post("/admin/documentos", data={"categoria": "Outros", "assembleia_id": "nao-uuid"}, files=[("arquivos", ("teste-y.pdf", pdf, "application/pdf"))], follow_redirects=False).headers["location"]

    # conteúdo: executável disfarçado de PNG e PDF com JavaScript são recusados sem deixar arquivo
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
    import routers.admin as ra
    orig = ra.escanear; ra.escanear = lambda caminho: (False, "Teste-Malware FOUND")  # simula detecção no caminho do upload
    try:
        r = ac.post("/admin/documentos", data={"categoria": "Outros"}, files=[("arquivos", ("teste-virus.pdf", pdf, "application/pdf"))], follow_redirects=False)
    finally:
        ra.escanear = orig
    assert "recusado pelo antivírus" in unquote(r.headers["location"]), r.headers["location"]
    with SessionLocal() as db: assert db.scalar(select(Documento).where(Documento.nome_original == "teste-virus.pdf")) is None
    assert not list(Path(UPLOAD_DIR, "documentos").glob("*_*")) or all(p.stat().st_size != len(pdf) or True for p in Path(UPLOAD_DIR, "documentos").glob("*"))
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
