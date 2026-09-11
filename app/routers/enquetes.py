"""Enquetes: cadastradas pela administração; 1 voto por apto; inadimplente vota sem contar (igual à assembleia);
resultado parcial visível ao condômino logo após votar."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import auth
from db import get_db
from financeiro import unidade_inadimplente
from mail import ip_de, registrar
from models import AdminUser, Enquete, EnqueteOpcao, EnqueteVoto, Unidade
from routers.admin import admin_dep
from routers.morador import morador_atual
from routers.votacao import MSG_INADIMPLENTE, agora, parse_dt

router = APIRouter()


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "agora": agora(), "MSG_INADIMPLENTE": MSG_INADIMPLENTE, **ctx})


def carregar(db: Session, eid) -> Enquete:
    e = db.scalar(select(Enquete).where(Enquete.id == eid).options(selectinload(Enquete.opcoes)))
    if not e:
        raise HTTPException(404)
    return e


def resultado(db: Session, e: Enquete) -> dict:
    linhas = db.execute(select(EnqueteVoto.opcao_id, EnqueteVoto.inadimplente_no_voto, func.count())
                        .where(EnqueteVoto.enquete_id == e.id).group_by(EnqueteVoto.opcao_id, EnqueteVoto.inadimplente_no_voto)).all()
    r = {"validos": {o.id: 0 for o in e.opcoes}, "nao_computados": 0, "total_validos": 0}
    for opcao_id, inad, n in linhas:
        if inad:
            r["nao_computados"] += n
        else:
            r["validos"][opcao_id] = n
            r["total_validos"] += n
    return r


def aberta(e: Enquete) -> bool:
    return e.abre_em <= agora() <= e.fecha_em


# ---------- administração ----------
@router.get("/admin/enquetes")
def admin_lista(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    todas = db.scalars(select(Enquete).order_by(Enquete.abre_em.desc())).all()
    return render(request, "admin/enquetes.html", enquetes=[e for e in todas if not e.excluido_em], excluidas=[e for e in todas if e.excluido_em])


@router.post("/admin/enquetes")
def admin_criar(request: Request, pergunta: str = Form(...), descricao: str = Form(""), opcoes: str = Form(...),
                abre_em: str = Form(...), fecha_em: str = Form(...), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    ops = [o.strip()[:200] for o in opcoes.splitlines() if o.strip()]
    if not pergunta.strip() or len(ops) < 2:
        raise HTTPException(400, "Informe a pergunta e ao menos duas opções, uma por linha")
    e = Enquete(pergunta=pergunta.strip()[:300], descricao=descricao.strip()[:4000] or None, abre_em=parse_dt(abre_em), fecha_em=parse_dt(fecha_em),
                criado_por=admin.login, criado_ip=ip_de(request))
    if e.fecha_em <= e.abre_em:
        raise HTTPException(400, "Fechamento deve ser depois da abertura")
    db.add(e)
    db.flush()
    db.add_all(EnqueteOpcao(enquete_id=e.id, ordem=i + 1, texto=o) for i, o in enumerate(ops))
    db.commit()
    registrar("Enquete criada", request, admin=admin.login, pergunta=e.pergunta, opcoes=" | ".join(ops), abre_em=e.abre_em, fecha_em=e.fecha_em)
    return RedirectResponse(f"/admin/enquetes/{e.id}", status_code=303)


@router.get("/admin/enquetes/{eid}")
def admin_detalhe(request: Request, eid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    e = carregar(db, eid)
    return render(request, "admin/enquete.html", e=e, res=resultado(db, e), aberta=aberta(e),
                  unidades_ativas=db.scalar(select(func.count()).select_from(Unidade).where(Unidade.ativa, Unidade.apto != "")))


@router.post("/admin/enquetes/{eid}/excluir")
def admin_excluir(request: Request, eid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    e = carregar(db, eid)
    if not e.excluido_em:  # exclusão lógica; votos ficam
        e.excluido_em, e.excluido_por, e.excluido_ip = agora(), admin.login, ip_de(request)
        db.commit()
        registrar("Enquete excluída (lógico)", request, admin=admin.login, pergunta=e.pergunta)
    return RedirectResponse("/admin/enquetes", status_code=303)


# ---------- condômino ----------
@router.get("/morador/enquetes")
def morador_lista(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    lista = db.scalars(select(Enquete).where(Enquete.excluido_em.is_(None)).order_by(Enquete.abre_em.desc()).limit(50)).all()
    votadas = {v.enquete_id for v in db.scalars(select(EnqueteVoto).where(EnqueteVoto.unidade_id == m.unidade_id))}
    return render(request, "morador/enquetes.html", morador=m, enquetes=lista, votadas=votadas)


@router.get("/morador/enquetes/{eid}")
def morador_detalhe(request: Request, eid: uuid.UUID, msg: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    e = carregar(db, eid)
    if e.excluido_em:
        raise HTTPException(404)
    voto = db.scalar(select(EnqueteVoto).where(EnqueteVoto.enquete_id == eid, EnqueteVoto.unidade_id == m.unidade_id))
    mostrar = voto is not None or agora() > e.fecha_em  # resultado parcial logo após votar; sempre após fechar
    return render(request, "morador/enquete.html", morador=m, e=e, voto=voto, aberta=aberta(e), msg=msg,
                  res=resultado(db, e) if mostrar else None, inadimplente=unidade_inadimplente(db, m.unidade_id))


@router.post("/morador/enquetes/{eid}/votar")
def morador_votar(request: Request, eid: uuid.UUID, opcao: uuid.UUID = Form(...), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    e = carregar(db, eid)
    if e.excluido_em or not aberta(e):
        raise HTTPException(400, "Enquete fechada")
    if opcao not in {o.id for o in e.opcoes}:
        raise HTTPException(400, "Opção inválida")
    if db.scalar(select(EnqueteVoto).where(EnqueteVoto.enquete_id == eid, EnqueteVoto.unidade_id == m.unidade_id)):
        return RedirectResponse(f"/morador/enquetes/{eid}?msg=Sua+unidade+já+votou+nesta+enquete.", status_code=303)
    inad = unidade_inadimplente(db, m.unidade_id)
    db.add(EnqueteVoto(enquete_id=eid, unidade_id=m.unidade_id, opcao_id=opcao, morador_id=m.id, inadimplente_no_voto=inad))
    db.commit()
    registrar("Voto em enquete", request, condomino=m.nome, unidade=m.unidade.rotulo, enquete=e.pergunta, inadimplente=inad)
    from urllib.parse import quote
    return RedirectResponse(f"/morador/enquetes/{eid}?msg={quote(MSG_INADIMPLENTE if inad else 'Voto registrado.')}", status_code=303)
