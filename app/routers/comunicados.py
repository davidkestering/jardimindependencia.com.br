"""Comunicados da administração: rascunho (só admin) | condominos (área logada) | publico (site)."""
import threading
import uuid
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import apns
import auth
import interfone
from config import SITE_URL, UPLOAD_DIR
from db import SessionLocal, get_db
from mail import FUSO, anotar, enviar, ip_de, registrar
from models import AdminUser, Comunicado, Documento, Morador
from routers.admin import ERRO_UPLOAD, MAX_TOTAL_MB, _gravar_em_blocos, admin_dep, exigir_proprio
from routers.morador import morador_atual

router = APIRouter()
VISIBILIDADES = ["rascunho", "condominos", "publico"]
EXT_DOC = {".pdf", ".docx"}  # documento opcional do comunicado; .doc/.docm nunca (macro)
CATEGORIA_DOC = "Comunicados"  # o anexo é um Documento desta categoria: aparece também na tela de Documentos
MESES = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
RESUMO_N = 200


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "resumo": resumo, "documento_de": documento_de, **ctx})


def resumo(texto: str, n: int = RESUMO_N) -> str:
    t = " ".join(texto.split())
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0] + "…"


def visiveis(db: Session, niveis: tuple[str, ...], limite: int | None = None):
    stmt = select(Comunicado).where(Comunicado.visibilidade.in_(niveis), Comunicado.excluido_em.is_(None)).order_by(Comunicado.publicado_em.desc())
    return db.scalars(stmt.limit(limite) if limite else stmt).all()


def publicos(db: Session, limite: int | None = None):
    return visiveis(db, ("publico",), limite)


def novos_para(db: Session, m: Morador) -> list[Comunicado]:
    lista = visiveis(db, ("condominos", "publico"))
    return [c for c in lista if not m.comunicados_vistos_em or c.publicado_em > m.comunicados_vistos_em]


def por_mes(lista):
    return [(f"{MESES[mes]} de {ano}", list(g)) for (ano, mes), g in groupby(lista, key=lambda c: (c.publicado_em.year, c.publicado_em.month))]


def notificar_comunicado(cid: uuid.UUID) -> None:
    """E-mail a cada condômino aprovado (1 por endereço) e push a quem ativou. Em thread; roda sempre que sai de rascunho."""
    def corpo():
        with SessionLocal() as db:
            c = db.get(Comunicado, cid)
            emails = sorted({m.email.lower() for m in db.scalars(select(Morador).where(Morador.status == "aprovado"))})
            assunto = f"[Jardim Independência] Comunicado: {c.titulo}"
            texto = f"{c.titulo}\n\n{c.texto}\n\n" + (f"Documento anexo: {SITE_URL}/morador/comunicados/{c.id}/documento\n\n" if documento_de(c) else "") \
                + f"Veja na sua área: {SITE_URL}/morador/comunicados/{c.id}"
            payload = {"titulo": f"Comunicado: {c.titulo}", "corpo": resumo(c.texto, 100), "url": f"/morador/comunicados/{c.id}", "tag": "comunicado"}
            titulo = c.titulo
        for e in emails:
            enviar(e, assunto, texto)
        interfone.push_para_todos(payload)
        apns.notificar("Novo comunicado", titulo, "/morador/comunicados")
    threading.Thread(target=corpo, daemon=True).start()


# ---------- administração ----------
@router.get("/admin/comunicados")
def admin_lista(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    todos = db.scalars(select(Comunicado).order_by(Comunicado.criado_em.desc())).all()
    return render(request, "admin/comunicados.html", comunicados=[c for c in todos if not c.excluido_em],
                  excluidos=[c for c in todos if c.excluido_em], visibilidades=VISIBILIDADES, max_mb=MAX_TOTAL_MB,
                  erro=request.query_params.get("erro"), ok=request.query_params.get("ok"))


def _validar(titulo: str, texto: str) -> tuple[str, str]:
    titulo, texto = titulo.strip()[:200], texto.strip()[:20000]
    if not titulo or not texto:
        raise HTTPException(400, "Título e texto são obrigatórios")
    return titulo, texto


def documento_de(c: Comunicado) -> Documento | None:
    """Documento vigente do comunicado (nenhum se foi excluído pela tela de Documentos)."""
    return c.documento if c.documento and not c.documento.excluido_em else None


async def _anexar_documento(request: Request, db: Session, c: Comunicado, arquivo: UploadFile | None, admin: AdminUser) -> str | None:
    """Grava o PDF/Word do comunicado (assinatura interna, conteúdo ativo/macro e antivírus em _gravar_em_blocos) como um Documento
    da categoria Comunicados. O anterior, se houver, fica excluído (lógico) na tela de Documentos. Devolve a mensagem de erro, ou None."""
    if not arquivo or not arquivo.filename:
        return None
    ext = Path(arquivo.filename).suffix.lower()
    if ext not in EXT_DOC:
        return "Envie o documento em PDF ou Word (.docx)"
    d = Documento(id=db.scalar(text("select uuidv7()")), categoria=CATEGORIA_DOC, competencia=datetime.now(FUSO).date(),
                  enviado_por=admin.login, enviado_ip=ip_de(request), publico=c.visibilidade != "rascunho")
    nome = f"documentos/{d.id}_{datetime.now(FUSO):%d%m%Y_%H%M%S}{ext}"
    provisorio = Path(UPLOAD_DIR) / f"documentos/novo_{uuid.uuid4()}{ext}"  # nunca sobrescreve o documento atual se o novo for recusado
    try:
        n = await _gravar_em_blocos(arquivo, provisorio, MAX_TOTAL_MB * 1024 * 1024)
    except Exception:  # conexão caiu no meio do envio: sem órfão em disco
        provisorio.unlink(missing_ok=True)
        raise
    if n < 0:
        return ERRO_UPLOAD[n].format(mb=MAX_TOTAL_MB, nome=arquivo.filename)
    provisorio.replace(Path(UPLOAD_DIR) / nome)
    d.titulo, d.arquivo, d.nome_original = c.titulo, nome, Path(arquivo.filename).name[:255]
    _excluir_documento(request, c, admin, "Substituído por outro documento no comunicado")
    c.documento = d
    return None


def _excluir_documento(request: Request, c: Comunicado, admin: AdminUser, motivo: str) -> None:
    """Exclusão lógica do Documento do comunicado (some da tela de Documentos, fica no histórico de lá)."""
    if d := documento_de(c):
        d.excluido_em, d.excluido_por, d.excluido_ip, d.excluido_motivo = datetime.now(timezone.utc), admin.login, ip_de(request), motivo


def _erro(destino: str, msg: str) -> RedirectResponse:
    return RedirectResponse(f"{destino}?erro={quote(msg)}", status_code=303)


def _servir_documento(request: Request, c: Comunicado | None, permitido: bool):
    d = documento_de(c) if c and permitido else None
    if not d:
        raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / d.arquivo).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    anotar("Comunicado: documento baixado", request, comunicado=c.titulo, arquivo=d.nome_original)
    return FileResponse(caminho, filename=d.nome_original)


def _mudar_visibilidade(request: Request, c: Comunicado, vis: str, admin: AdminUser, db: Session) -> None:
    if vis not in VISIBILIDADES:
        raise HTTPException(400, "Visibilidade inválida")
    saiu_de_rascunho = c.visibilidade == "rascunho" and vis != "rascunho"  # condôminos -> público não reavisa
    c.visibilidade = vis
    if d := documento_de(c):
        d.publico = vis != "rascunho"  # na tela de Documentos só aparece quando o comunicado está publicado
    if saiu_de_rascunho and c.publicado_em is None:
        c.publicado_em, c.publicado_por, c.publicado_ip = datetime.now(timezone.utc), admin.login, ip_de(request)
    db.commit()
    notificar = saiu_de_rascunho and not admin.teste  # usuário de teste nunca dispara e-mail/push para os condôminos
    registrar(f"Comunicado {vis}", request, admin=admin.login, titulo=c.titulo, notificado="sim" if notificar else "não")
    if notificar:
        notificar_comunicado(c.id)


@router.post("/admin/comunicados")
async def admin_criar(request: Request, titulo: str = Form(...), texto: str = Form(...), visibilidade: str = Form("rascunho"),
                      arquivo: UploadFile | None = File(None), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    titulo, texto = _validar(titulo, texto)
    c = Comunicado(titulo=titulo, texto=texto, autor=admin.login, criado_ip=ip_de(request))
    if erro := await _anexar_documento(request, db, c, arquivo, admin):
        return _erro("/admin/comunicados", erro)
    db.add(c)
    db.commit()
    d = documento_de(c)
    registrar("Comunicado criado", request, admin=admin.login, titulo=c.titulo, documento=d.nome_original if d else "—")
    _mudar_visibilidade(request, c, visibilidade, admin, db)
    return RedirectResponse("/admin/comunicados?ok=" + quote("Comunicado salvo" + (f" com o documento {d.nome_original}" if d else "")), status_code=303)


@router.get("/admin/comunicados/{cid}/documento")
def admin_documento(request: Request, cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return _servir_documento(request, db.get(Comunicado, cid), True)


@router.get("/admin/comunicados/{cid}")
def admin_editar(request: Request, cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    return render(request, "admin/comunicado.html", c=c, visibilidades=VISIBILIDADES, max_mb=MAX_TOTAL_MB,
                  erro=request.query_params.get("erro"), ok=request.query_params.get("ok"))


@router.get("/admin/comunicados/{cid}/preview")
def admin_preview(request: Request, cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Mostra o comunicado como o leitor verá, mesmo em rascunho, com os botões de publicação."""
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    return render(request, "admin/comunicado_preview.html", c=c, visibilidades=VISIBILIDADES)


@router.post("/admin/comunicados/{cid}")
async def admin_salvar(request: Request, cid: uuid.UUID, titulo: str = Form(...), texto: str = Form(...), remover_documento: str = Form(""),
                       arquivo: UploadFile | None = File(None), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    exigir_proprio(admin, c.autor)
    d0 = documento_de(c)
    antes = (c.titulo, c.texto, d0.nome_original if d0 else None)
    c.titulo, c.texto = _validar(titulo, texto)
    if erro := await _anexar_documento(request, db, c, arquivo, admin):
        db.rollback()
        return _erro(f"/admin/comunicados/{c.id}", erro)
    if remover_documento and not (arquivo and arquivo.filename):
        _excluir_documento(request, c, admin, "Removido do comunicado pela administração")
        c.documento = None
    if d := documento_de(c):
        d.titulo = c.titulo
    db.commit()
    registrar("Comunicado editado", request, admin=admin.login, titulo=c.titulo, titulo_anterior=antes[0], texto_anterior=antes[1][:500],
              documento=d.nome_original if d else "—", documento_anterior=antes[2] or "—")
    return RedirectResponse(f"/admin/comunicados/{c.id}?ok=" + quote("Alterações salvas"), status_code=303)


@router.post("/admin/comunicados/{cid}/visibilidade")
def admin_visibilidade(request: Request, cid: uuid.UUID, visibilidade: str = Form(...),
                       admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    exigir_proprio(admin, c.autor)
    _mudar_visibilidade(request, c, visibilidade, admin, db)
    return RedirectResponse("/admin/comunicados", status_code=303)


@router.post("/admin/comunicados/{cid}/excluir")
def admin_excluir(request: Request, cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Exclusão lógica: some do site e da área do condômino; fica no histórico da administração."""
    c = db.get(Comunicado, cid)
    if c and not c.excluido_em:
        exigir_proprio(admin, c.autor)
        c.excluido_em, c.excluido_por, c.excluido_ip = datetime.now(timezone.utc), admin.login, ip_de(request)
        _excluir_documento(request, c, admin, "Comunicado excluído")
        db.commit()
        registrar("Comunicado excluído (lógico)", request, admin=admin.login, titulo=c.titulo, visibilidade=c.visibilidade)
    return RedirectResponse("/admin/comunicados", status_code=303)


# ---------- condômino ----------
@router.get("/morador/comunicados")
def morador_lista(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    novos = {c.id for c in novos_para(db, m)}
    lista = visiveis(db, ("condominos", "publico"))
    m.comunicados_vistos_em = datetime.now(timezone.utc)
    db.commit()
    anotar("Comunicados: página acessada", request, novos=len(novos))
    return render(request, "morador/comunicados.html", morador=m, grupos=por_mes(lista), novos_ids=novos)


@router.get("/morador/comunicados/{cid}")
def morador_ver(request: Request, cid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    c = db.get(Comunicado, cid)
    if not c or c.visibilidade == "rascunho" or c.excluido_em:
        raise HTTPException(404)
    anotar("Comunicado lido", request, comunicado=c.titulo)
    return render(request, "morador/comunicado.html", morador=m, c=c)


@router.get("/morador/comunicados/{cid}/documento")
def morador_documento(request: Request, cid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    morador_atual(request, db, sessao)
    c = db.get(Comunicado, cid)
    return _servir_documento(request, c, bool(c and c.visibilidade != "rascunho" and not c.excluido_em))


# ---------- site ----------
@router.get("/comunicados")
def site_lista(request: Request, db: Session = Depends(get_db)):
    return render(request, "site/comunicados.html", grupos=por_mes(publicos(db)))


@router.get("/comunicados/{cid}")
def site_ver(request: Request, cid: uuid.UUID, db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid)
    if not c or c.visibilidade != "publico" or c.excluido_em:
        raise HTTPException(404)
    return render(request, "site/comunicado.html", c=c)


@router.get("/comunicados/{cid}/documento")
def site_documento(request: Request, cid: uuid.UUID, db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid)
    return _servir_documento(request, c, bool(c and c.visibilidade == "publico" and not c.excluido_em))


if __name__ == "__main__":
    assert resumo("a b c", 10) == "a b c"
    assert resumo("palavra " * 50, 20) == "palavra palavra…"
    print("comunicados ok")
