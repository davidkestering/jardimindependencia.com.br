import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
from db import get_db
from mail import registrar
from models import AdminUser, Inadimplencia, Unidade
from routers.admin import admin_dep

router = APIRouter()


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


def mapa_unidades(db: Session) -> dict[str, list[str]]:
    mapa: dict[str, list[str]] = {}
    for u in db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.ativa).order_by(Unidade.bloco, Unidade.apto)):
        mapa.setdefault(u.bloco, []).append(u.apto)
    return mapa


@router.get("/admin/financeiro")
def admin_lista(request: Request, erro: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    base = select(Inadimplencia).join(Unidade)
    ativas = db.scalars(base.where(Inadimplencia.encerrado_em.is_(None)).order_by(Unidade.bloco, Unidade.apto)).all()
    historico = db.scalars(base.where(Inadimplencia.encerrado_em.is_not(None)).order_by(Inadimplencia.encerrado_em.desc()).limit(200)).all()
    return render(request, "admin/financeiro.html", ativas=ativas, historico=historico, mapa=mapa_unidades(db), erro=erro)


@router.post("/admin/financeiro")
def admin_registrar(request: Request, bloco: str = Form(...), apto: str = Form(...), observacao: str = Form(...),
                    admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    observacao = observacao.strip()[:2000]
    u = db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == auth.so_digitos(apto).zfill(3)[-3:], Unidade.ativa))
    if not u:
        return RedirectResponse("/admin/financeiro?erro=Unidade+não+encontrada", status_code=303)
    if not observacao:
        return RedirectResponse("/admin/financeiro?erro=A+observação+é+obrigatória", status_code=303)
    db.add(Inadimplencia(unidade_id=u.id, observacao=observacao, registrado_por=admin.login))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/admin/financeiro?erro={u.rotulo}+já+está+registrada+como+inadimplente", status_code=303)
    registrar("Inadimplência registrada", request, admin=admin.login, unidade=u.rotulo, observacao=observacao)
    return RedirectResponse("/admin/financeiro", status_code=303)


@router.post("/admin/financeiro/{iid}/encerrar")
def admin_encerrar(request: Request, iid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    i = db.get(Inadimplencia, iid)
    if not i or i.encerrado_em:
        raise HTTPException(404)
    i.encerrado_em, i.encerrado_por = datetime.now(timezone.utc), admin.login
    db.commit()
    registrar("Inadimplência encerrada", request, admin=admin.login, unidade=i.unidade.rotulo)
    return RedirectResponse("/admin/financeiro", status_code=303)
