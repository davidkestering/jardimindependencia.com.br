import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import auth
from db import get_db
from financeiro import em_atraso, gerar_cobranca
from models import AdminUser, Cobranca, Unidade
from routers.admin import admin_dep
from routers.morador import morador_atual

router = APIRouter()


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "hoje": date.today(), "em_atraso": em_atraso, **ctx})


def parse_valor(v: str) -> Decimal:
    try:
        d = Decimal(v.replace(".", "").replace(",", ".")) if "," in v else Decimal(v)
    except InvalidOperation:
        raise HTTPException(400, "Valor inválido")
    if d <= 0:
        raise HTTPException(400, "Valor deve ser positivo")
    return d.quantize(Decimal("0.01"))


# ---------- administração ----------
@router.get("/admin/financeiro")
def admin_lista(request: Request, status: str = "aberta", bloco: str = "", apto: str = "", atraso: str = "",
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    stmt = select(Cobranca).join(Unidade).order_by(Cobranca.vencimento.desc(), Unidade.bloco, Unidade.apto)
    if status:
        stmt = stmt.where(Cobranca.status == status)
    if bloco:
        stmt = stmt.where(Unidade.bloco == bloco)
    if apto:
        stmt = stmt.where(Unidade.apto == auth.so_digitos(apto).zfill(3))
    if atraso:
        stmt = stmt.where(Cobranca.status == "aberta", Cobranca.vencimento < date.today())
    cobrancas = db.scalars(stmt.limit(500)).all()
    inadimplentes = db.execute(
        select(Unidade.bloco, Unidade.apto, func.count(), func.sum(Cobranca.valor)).join(Cobranca)
        .where(Cobranca.status == "aberta", Cobranca.vencimento < date.today())
        .group_by(Unidade.bloco, Unidade.apto).order_by(Unidade.bloco, Unidade.apto)).all()
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != ""))})
    return render(request, "admin/financeiro.html", cobrancas=cobrancas, inadimplentes=inadimplentes, blocos=blocos,
                  status=status, bloco=bloco, apto=apto, atraso=atraso, total=sum((c.valor for c in cobrancas), Decimal(0)))


@router.post("/admin/financeiro")
def admin_criar(descricao: str = Form(...), valor: str = Form(...), vencimento: str = Form(...),
                bloco: str = Form(""), apto: str = Form(""), lote: str = Form(""),
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    venc = auth.parse_data(vencimento)
    if not venc:
        raise HTTPException(400, "Vencimento inválido")
    val = parse_valor(valor)
    if lote:
        unidades = db.scalars(select(Unidade).where(Unidade.ativa, Unidade.apto != "")).all()
    else:
        u = db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == auth.so_digitos(apto).zfill(3)))
        if not u:
            raise HTTPException(400, "Unidade não encontrada")
        unidades = [u]
    for u in unidades:
        c = Cobranca(unidade_id=u.id, descricao=descricao.strip()[:200], valor=val, vencimento=venc)
        gerar_cobranca(c)
        db.add(c)
    db.commit()
    return RedirectResponse("/admin/financeiro", status_code=303)


@router.post("/admin/financeiro/{cid}/status")
def admin_status(cid: uuid.UUID, status: str = Form(...), admin: AdminUser = Depends(admin_dep),
                 db: Session = Depends(get_db)):
    if status not in ("paga", "aberta", "cancelada"):
        raise HTTPException(400)
    c = db.get(Cobranca, cid)
    if not c:
        raise HTTPException(404)
    c.status = status
    c.pago_em = datetime.now(timezone.utc) if status == "paga" else None
    db.commit()
    return RedirectResponse("/admin/financeiro", status_code=303)


# ---------- condômino ----------
@router.get("/morador/financeiro")
def morador_financeiro(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    cobrancas = db.scalars(select(Cobranca).where(Cobranca.unidade_id == m.unidade_id)
                           .order_by(Cobranca.vencimento.desc())).all()
    abertas = [c for c in cobrancas if c.status == "aberta"]
    atrasadas = [c for c in abertas if em_atraso(c)]
    return render(request, "morador/financeiro.html", morador=m, cobrancas=cobrancas, abertas=abertas, atrasadas=atrasadas,
                  total_atraso=sum((c.valor for c in atrasadas), Decimal(0)))
