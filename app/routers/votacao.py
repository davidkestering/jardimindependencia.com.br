import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import auth
from db import get_db
from financeiro import unidade_inadimplente
from models import AdminUser, Assembleia, Documento, Opcao, Pauta, Unidade, Voto
from routers.admin import admin_dep
from routers.morador import morador_atual

router = APIRouter()
MSG_INADIMPLENTE = "Voto apresentado, porém a unidade está com registro de inadimplência."


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "agora": agora(), **ctx})


def agora():
    return datetime.now(timezone.utc)


def parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone(__import__("datetime").timedelta(hours=-3)))
    except ValueError:
        raise HTTPException(400, "Data/hora inválida")


def resultado(db: Session, assembleia: Assembleia) -> dict:
    """Por pauta: contagem de votos válidos por opção e total de votos não computados (inadimplência)."""
    linhas = db.execute(
        select(Voto.pauta_id, Voto.opcao_id, Voto.inadimplente_no_voto, func.count())
        .join(Pauta).where(Pauta.assembleia_id == assembleia.id)
        .group_by(Voto.pauta_id, Voto.opcao_id, Voto.inadimplente_no_voto)).all()
    r = {p.id: {"validos": {o.id: 0 for o in p.opcoes}, "nao_computados": 0, "total_validos": 0} for p in assembleia.pautas}
    for pauta_id, opcao_id, inad, n in linhas:
        if inad:
            r[pauta_id]["nao_computados"] += n
        else:
            r[pauta_id]["validos"][opcao_id] = n
            r[pauta_id]["total_validos"] += n
    return r


def carregar(db: Session, aid) -> Assembleia:
    a = db.scalar(select(Assembleia).where(Assembleia.id == aid)
                  .options(selectinload(Assembleia.pautas).selectinload(Pauta.opcoes)))
    if not a:
        raise HTTPException(404)
    return a


# ---------- administração ----------
@router.get("/admin/assembleias")
def admin_lista(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    lista = db.scalars(select(Assembleia).order_by(Assembleia.abre_em.desc())).all()
    return render(request, "admin/assembleias.html", assembleias=lista)


@router.post("/admin/assembleias")
def admin_criar(titulo: str = Form(...), abre_em: str = Form(...), fecha_em: str = Form(...),
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    a = Assembleia(titulo=titulo.strip()[:200], abre_em=parse_dt(abre_em), fecha_em=parse_dt(fecha_em))
    if a.fecha_em <= a.abre_em:
        raise HTTPException(400, "Fechamento deve ser depois da abertura")
    db.add(a)
    db.commit()
    return RedirectResponse(f"/admin/assembleias/{a.id}", status_code=303)


@router.get("/admin/assembleias/{aid}")
def admin_detalhe(request: Request, aid: uuid.UUID, erro: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    a = carregar(db, aid)
    docs = db.scalars(select(Documento).where(Documento.assembleia_id == aid).order_by(Documento.criado_em)).all()
    from routers.admin import CATEGORIAS, MAX_TOTAL_MB
    return render(request, "admin/assembleia.html", a=a, res=resultado(db, a), documentos=docs, erro=erro, categorias=CATEGORIAS, max_mb=MAX_TOTAL_MB,
                  unidades_ativas=db.scalar(select(func.count()).select_from(Unidade).where(Unidade.ativa, Unidade.apto != "")))


@router.post("/admin/assembleias/{aid}/pautas")
def admin_pauta(aid: uuid.UUID, texto: str = Form(...), opcoes: str = Form(...),
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    a = carregar(db, aid)
    ops = [o.strip()[:200] for o in opcoes.splitlines() if o.strip()]
    if len(ops) < 2:
        raise HTTPException(400, "Informe ao menos duas opções, uma por linha")
    p = Pauta(assembleia_id=a.id, ordem=len(a.pautas) + 1, texto=texto.strip()[:2000])
    p.opcoes = [Opcao(ordem=i + 1, texto=o) for i, o in enumerate(ops)]
    db.add(p)
    db.commit()
    return RedirectResponse(f"/admin/assembleias/{aid}", status_code=303)


@router.post("/admin/assembleias/{aid}/pautas/{pid}/excluir")
def admin_pauta_excluir(aid: uuid.UUID, pid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    p = db.get(Pauta, pid)
    if p and p.assembleia_id == aid:
        db.delete(p)
        db.commit()
    return RedirectResponse(f"/admin/assembleias/{aid}", status_code=303)


@router.post("/admin/assembleias/{aid}/excluir")
def admin_excluir(aid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    a = db.get(Assembleia, aid)
    if a:
        db.delete(a)
        db.commit()
    return RedirectResponse("/admin/assembleias", status_code=303)


# ---------- condômino ----------
@router.get("/morador/assembleias")
def morador_lista(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    lista = db.scalars(select(Assembleia).order_by(Assembleia.abre_em.desc()).limit(50)).all()
    return render(request, "morador/assembleias.html", morador=m, assembleias=lista)


@router.get("/morador/assembleias/{aid}")
def morador_detalhe(request: Request, aid: uuid.UUID, msg: str = "", sessao: dict = Depends(auth.exigir("morador")),
                    db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    a = carregar(db, aid)
    votos = {v.pauta_id: v for v in db.scalars(select(Voto).join(Pauta).where(Pauta.assembleia_id == aid, Voto.unidade_id == m.unidade_id))}
    aberta = a.abre_em <= agora() <= a.fecha_em
    docs = db.scalars(select(Documento).where(Documento.assembleia_id == aid, Documento.publico).order_by(Documento.criado_em)).all()
    return render(request, "morador/assembleia.html", morador=m, a=a, votos=votos, aberta=aberta, documentos=docs,
                  res=resultado(db, a) if agora() > a.fecha_em else None, msg=msg,
                  inadimplente=unidade_inadimplente(db, m.unidade_id), MSG_INADIMPLENTE=MSG_INADIMPLENTE)


@router.post("/morador/assembleias/{aid}/votar")
async def morador_votar(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")),
                        db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    a = carregar(db, aid)
    if not (a.abre_em <= agora() <= a.fecha_em):
        raise HTTPException(400, "Votação fechada")
    form = await request.form()
    inad = unidade_inadimplente(db, m.unidade_id)
    ja = {v.pauta_id for v in db.scalars(select(Voto).join(Pauta).where(Pauta.assembleia_id == aid, Voto.unidade_id == m.unidade_id))}
    registrados = 0
    for p in a.pautas:
        escolha = form.get(f"pauta_{p.id}")
        if not escolha or p.id in ja:
            continue
        try:
            oid = uuid.UUID(escolha)
        except ValueError:
            continue
        if oid not in {o.id for o in p.opcoes}:
            continue
        db.add(Voto(unidade_id=m.unidade_id, pauta_id=p.id, opcao_id=oid, morador_id=m.id, inadimplente_no_voto=inad))
        registrados += 1
    db.commit()
    if not registrados:
        msg = "Nenhum voto novo registrado."
    elif inad:
        msg = MSG_INADIMPLENTE
    else:
        msg = "Voto registrado."
    from urllib.parse import quote
    return RedirectResponse(f"/morador/assembleias/{aid}?msg={quote(msg)}", status_code=303)
