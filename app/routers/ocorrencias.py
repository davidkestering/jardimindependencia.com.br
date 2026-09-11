"""Ocorrências: o condômino registra (imutável: sem edição nem exclusão), a administração responde, os dois anexam
arquivos, e só o condômino finaliza. E-mails: registro -> contato@ + cópia ao condômino; resposta da administração ->
condômino; mensagem do condômino -> contato@. Aviso na área do condômino quando há resposta não vista."""
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

import auth
from config import MAIL_CONTATO, SITE_URL, UPLOAD_DIR
from db import get_db
from mail import FUSO, ip_de, notificar, registrar
from models import AdminUser, Morador, Ocorrencia, OcorrenciaAnexo, OcorrenciaMensagem
from termo import TERMO_OCORRENCIA
from routers.admin import ERRO_UPLOAD, EXT_OK, MAX_TOTAL_MB, _gravar_em_blocos, admin_dep
from routers.morador import morador_atual

router = APIRouter()
ASSUNTO = "[Jardim Independência] Ocorrência nº {n}: {t}"


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "max_mb": MAX_TOTAL_MB, "captcha": auth.captcha_novo(), **ctx})


def carregar(db: Session, oid) -> Ocorrencia:
    o = db.scalar(select(Ocorrencia).where(Ocorrencia.id == oid)
                  .options(selectinload(Ocorrencia.mensagens).selectinload(OcorrenciaMensagem.anexos)))
    if not o:
        raise HTTPException(404)
    return o


async def _anexar(db: Session, msg: OcorrenciaMensagem, arquivos: list[UploadFile]) -> str | None:
    """Grava anexos em blocos (mesma regra dos documentos). Devolve mensagem de erro ou None."""
    arquivos = [a for a in arquivos if a and a.filename]
    if any(Path(a.filename).suffix.lower() not in EXT_OK for a in arquivos):
        return "Anexos: envie apenas PDF, JPG ou PNG"
    restante, gravados = MAX_TOTAL_MB * 1024 * 1024, []
    for a in arquivos:
        aid = db.scalar(text("select uuidv7()"))
        nome = f"ocorrencias/{aid}_{datetime.now(FUSO):%d%m%Y_%H%M%S}{Path(a.filename).suffix.lower()}"
        destino = Path(UPLOAD_DIR) / nome
        gravados.append(destino)
        n = await _gravar_em_blocos(a, destino, restante)
        if n < 0:
            for g in gravados:
                g.unlink(missing_ok=True)
            return ERRO_UPLOAD[n].format(mb=MAX_TOTAL_MB, nome=a.filename).replace("O envio passou", "Os anexos passaram")
        restante -= n
        db.add(OcorrenciaAnexo(id=aid, mensagem_id=msg.id, arquivo=nome, nome_original=Path(a.filename).name[:255]))
    return None


def _corpo(o: Ocorrencia, msg: OcorrenciaMensagem, cabecalho: str, link: str) -> str:
    anexos = "".join(f"\n- anexo: {a.nome_original}" for a in msg.anexos)
    return (f"{cabecalho}\n\nOcorrência nº {o.numero}: {o.titulo}\nUnidade: {o.unidade.rotulo}\nCondômino: {o.morador.nome}\n"
            f"Por: {msg.autor} em {msg.criado_em.astimezone(FUSO):%d/%m/%Y %H:%M}\n\n{msg.texto}{anexos}\n\n{link}")


def _servir(db: Session, aid: uuid.UUID, morador: Morador | None) -> FileResponse:
    a = db.get(OcorrenciaAnexo, aid)
    if not a:
        raise HTTPException(404)
    if morador is not None:  # condômino só vê anexos das ocorrências do seu apto
        m = db.get(OcorrenciaMensagem, a.mensagem_id)
        o = db.get(Ocorrencia, m.ocorrencia_id)
        if o.unidade_id != morador.unidade_id:
            raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / a.arquivo).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    return FileResponse(caminho, filename=a.nome_original)


# ---------- condômino ----------
@router.get("/morador/ocorrencias")
def morador_lista(request: Request, erro: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    lista = db.scalars(select(Ocorrencia).where(Ocorrencia.unidade_id == m.unidade_id).order_by(Ocorrencia.criado_em.desc())).all()
    return render(request, "morador/ocorrencias.html", morador=m, ocorrencias=lista, erro=erro)


@router.post("/morador/ocorrencias")
async def morador_registrar(request: Request, titulo: str = Form(...), texto: str = Form(...), arquivos: list[UploadFile] = File([]),
                            declaracao: str = Form(""), captcha: str = Form(""), captcha_token: str = Form(""),
                            sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    titulo, texto = titulo.strip()[:200], texto.strip()[:10000]
    if not auth.captcha_ok(captcha_token, captcha):
        return RedirectResponse("/morador/ocorrencias?erro=Resposta+da+conta+de+verificação+incorreta.+Tente+novamente.", status_code=303)
    if not declaracao:
        return RedirectResponse("/morador/ocorrencias?erro=É+preciso+aceitar+a+declaração+de+responsabilidade.", status_code=303)
    if not titulo or not texto:
        return RedirectResponse("/morador/ocorrencias?erro=Informe+título+e+descrição", status_code=303)
    agora, ip = datetime.now(timezone.utc), ip_de(request)
    o = Ocorrencia(unidade_id=m.unidade_id, morador_id=m.id, titulo=titulo, criado_ip=ip, ultima_msg_morador_em=agora,
                   termo_texto=TERMO_OCORRENCIA, termo_aceito_em=agora, termo_ip=ip)
    db.add(o)
    db.flush()
    msg = OcorrenciaMensagem(ocorrencia_id=o.id, autor_tipo="morador", autor=m.nome, texto=texto, ip=ip)
    db.add(msg)
    db.flush()
    if erro := await _anexar(db, msg, arquivos):
        db.rollback()
        return RedirectResponse(f"/morador/ocorrencias?erro={erro}", status_code=303)
    db.commit()
    db.expire_all()  # recarrega mensagens/anexos recém-gravados (a sessão guardava a lista antiga)
    o = carregar(db, o.id)
    msg = o.mensagens[0]
    registrar("Ocorrência registrada", request, numero=o.numero, condomino=m.nome, unidade=o.unidade.rotulo, titulo=o.titulo, anexos=len(msg.anexos), declaracao_aceita="sim")
    notificar(MAIL_CONTATO, ASSUNTO.format(n=o.numero, t=o.titulo), _corpo(o, msg, "Nova ocorrência registrada pelo condômino.", f"Responder: {SITE_URL}/admin/ocorrencias/{o.id}"))
    notificar(m.email, ASSUNTO.format(n=o.numero, t=o.titulo), _corpo(o, msg, "Cópia do registro da sua ocorrência. A administração foi avisada.", f"Acompanhar: {SITE_URL}/morador/ocorrencias/{o.id}"))
    return RedirectResponse(f"/morador/ocorrencias/{o.id}", status_code=303)


@router.get("/morador/ocorrencias/{oid}")
def morador_ver(request: Request, oid: uuid.UUID, erro: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    o = carregar(db, oid)
    if o.unidade_id != m.unidade_id:
        raise HTTPException(404)
    o.vista_pelo_morador_em = datetime.now(timezone.utc)  # zera o aviso de resposta nova
    db.commit()
    return render(request, "morador/ocorrencia.html", morador=m, o=o, erro=erro)


@router.post("/morador/ocorrencias/{oid}/mensagem")
async def morador_mensagem(request: Request, oid: uuid.UUID, texto: str = Form(...), arquivos: list[UploadFile] = File([]),
                           sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    o = carregar(db, oid)
    if o.unidade_id != m.unidade_id:
        raise HTTPException(404)
    if o.status != "aberta":
        raise HTTPException(400, "Ocorrência finalizada: não aceita novas mensagens")
    texto = texto.strip()[:10000]
    if not texto:
        return RedirectResponse(f"/morador/ocorrencias/{oid}?erro=Escreva+a+mensagem", status_code=303)
    msg = OcorrenciaMensagem(ocorrencia_id=o.id, autor_tipo="morador", autor=m.nome, texto=texto, ip=ip_de(request))
    db.add(msg)
    db.flush()
    if erro := await _anexar(db, msg, arquivos):
        db.rollback()
        return RedirectResponse(f"/morador/ocorrencias/{oid}?erro={erro}", status_code=303)
    o.ultima_msg_morador_em = datetime.now(timezone.utc)
    db.commit()
    db.expire_all()  # recarrega mensagens/anexos recém-gravados (a sessão guardava a lista antiga)
    o = carregar(db, oid)
    msg = o.mensagens[-1]
    registrar("Mensagem do condômino na ocorrência", request, numero=o.numero, condomino=m.nome, unidade=o.unidade.rotulo, anexos=len(msg.anexos))
    notificar(MAIL_CONTATO, ASSUNTO.format(n=o.numero, t=o.titulo), _corpo(o, msg, "Nova mensagem do condômino na ocorrência.", f"Responder: {SITE_URL}/admin/ocorrencias/{o.id}"))
    return RedirectResponse(f"/morador/ocorrencias/{oid}", status_code=303)


@router.post("/morador/ocorrencias/{oid}/finalizar")
def morador_finalizar(request: Request, oid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    o = carregar(db, oid)
    if o.unidade_id != m.unidade_id:
        raise HTTPException(404)
    if o.status == "aberta":
        o.status, o.finalizada_em, o.finalizada_ip = "finalizada", datetime.now(timezone.utc), ip_de(request)
        db.commit()
        registrar("Ocorrência finalizada pelo condômino", request, numero=o.numero, condomino=m.nome, unidade=o.unidade.rotulo, titulo=o.titulo)
        notificar(MAIL_CONTATO, ASSUNTO.format(n=o.numero, t=o.titulo) + " — finalizada",
                  f"O condômino {m.nome} ({o.unidade.rotulo}) finalizou a ocorrência nº {o.numero}: {o.titulo}.\nNão há mais troca de mensagens.\n{SITE_URL}/admin/ocorrencias/{o.id}")
    return RedirectResponse(f"/morador/ocorrencias/{oid}", status_code=303)


@router.get("/morador/ocorrencias/anexo/{aid}")
def morador_anexo(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    return _servir(db, aid, morador_atual(request, db, sessao))


# ---------- administração ----------
@router.get("/admin/ocorrencias")
def admin_lista(request: Request, status: str = "aberta", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    stmt = select(Ocorrencia).order_by(Ocorrencia.criado_em.desc())
    if status in ("aberta", "finalizada"):
        stmt = stmt.where(Ocorrencia.status == status)
    return render(request, "admin/ocorrencias.html", ocorrencias=db.scalars(stmt.limit(500)).all(), status=status)


@router.get("/admin/ocorrencias/{oid}")
def admin_ver(request: Request, oid: uuid.UUID, erro: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    o = carregar(db, oid)
    o.vista_pela_admin_em = datetime.now(timezone.utc)
    db.commit()
    return render(request, "admin/ocorrencia.html", o=o, erro=erro)


@router.post("/admin/ocorrencias/{oid}/mensagem")
async def admin_responder(request: Request, oid: uuid.UUID, texto: str = Form(...), arquivos: list[UploadFile] = File([]),
                          admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    o = carregar(db, oid)
    if o.status != "aberta":
        raise HTTPException(400, "Ocorrência finalizada pelo condômino: não aceita novas mensagens")
    texto = texto.strip()[:10000]
    if not texto:
        return RedirectResponse(f"/admin/ocorrencias/{oid}?erro=Escreva+a+resposta", status_code=303)
    msg = OcorrenciaMensagem(ocorrencia_id=o.id, autor_tipo="admin", autor=admin.login, texto=texto, ip=ip_de(request))
    db.add(msg)
    db.flush()
    if erro := await _anexar(db, msg, arquivos):
        db.rollback()
        return RedirectResponse(f"/admin/ocorrencias/{oid}?erro={erro}", status_code=303)
    o.ultima_resposta_admin_em = datetime.now(timezone.utc)
    db.commit()
    db.expire_all()  # recarrega mensagens/anexos recém-gravados (a sessão guardava a lista antiga)
    o = carregar(db, oid)
    msg = o.mensagens[-1]
    registrar("Resposta da administração na ocorrência", request, admin=admin.login, numero=o.numero, unidade=o.unidade.rotulo, anexos=len(msg.anexos))
    notificar(o.morador.email, ASSUNTO.format(n=o.numero, t=o.titulo) + " — resposta da administração",
              _corpo(o, msg, "A administração respondeu à sua ocorrência.", f"Ver na sua área: {SITE_URL}/morador/ocorrencias/{o.id}"))
    return RedirectResponse(f"/admin/ocorrencias/{oid}", status_code=303)


@router.get("/admin/ocorrencias/anexo/{aid}")
def admin_anexo(aid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return _servir(db, aid, None)
