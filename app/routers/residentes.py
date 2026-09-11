"""Residentes (moradores e inquilinos) cadastrados pelo titular do apto e transferência do acesso.
Regra: por apto só 1 CPF entra na área do condômino (o titular). Residentes não fazem login."""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
from config import MAIL_CONTATO, SITE_URL
from db import get_db
from mail import FUSO, ip_de, notificar, registrar
from models import Morador, Residente
from routers.morador import morador_atual, render, validar_contato

router = APIRouter(prefix="/morador/residentes")
TIPOS = {"morador": "Morador", "inquilino": "Inquilino"}


def lista(db: Session, unidade_id):
    return db.scalars(select(Residente).where(Residente.unidade_id == unidade_id).order_by(Residente.nome)).all()


def _residente_do_apto(db: Session, rid: uuid.UUID, m: Morador) -> Residente:
    r = db.get(Residente, rid)
    if not r or r.unidade_id != m.unidade_id:
        raise HTTPException(404)
    return r


@router.get("")
def residentes(request: Request, erro: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    return render(request, "morador/residentes.html", morador=m, residentes=lista(db, m.unidade_id), tipos=TIPOS, erro=erro)


@router.post("")
def cadastrar(request: Request, nome: str = Form(...), cpf: str = Form(...), nascimento: str = Form(...), email: str = Form(...),
              telefone: str = Form(...), tipo: str = Form(...), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    cpf_d, nasc = auth.so_digitos(cpf), auth.parse_data(nascimento)
    erro = None
    if tipo not in TIPOS:
        erro = "Tipo inválido."
    elif not nome.strip():
        erro = "Informe o nome."
    elif not auth.cpf_valido(cpf_d):
        erro = "CPF inválido."
    elif cpf_d == m.cpf:
        erro = "Este CPF é o do titular do acesso."
    elif not nasc or nasc > date.today():
        erro = "Data de nascimento inválida."
    elif e := validar_contato(email, telefone):
        erro = e
    elif ja := db.scalar(select(Residente).where(Residente.unidade_id == m.unidade_id, Residente.cpf == cpf_d)):
        erro = f"CPF já cadastrado neste apartamento em nome de {ja.nome}."
    if erro:
        return render(request, "morador/residentes.html", morador=m, residentes=lista(db, m.unidade_id), tipos=TIPOS, erro=erro)
    r = Residente(unidade_id=m.unidade_id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc, email=email.strip()[:160],
                  telefone=telefone.strip()[:20], tipo=tipo, cadastrado_por=m.nome, cadastrado_ip=ip_de(request))
    db.add(r)
    db.commit()
    registrar("Residente cadastrado pelo condômino", request, titular=m.nome, unidade=m.unidade.rotulo, nome=r.nome, cpf=r.cpf_fmt, tipo=tipo)
    return RedirectResponse("/morador/residentes", status_code=303)


@router.post("/{rid}/excluir")
def excluir(request: Request, rid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    r = _residente_do_apto(db, rid, m)
    registrar("Residente removido pelo condômino", request, titular=m.nome, unidade=m.unidade.rotulo, nome=r.nome, cpf=r.cpf_fmt)
    db.delete(r)
    db.commit()
    return RedirectResponse("/morador/residentes", status_code=303)


@router.post("/{rid}/transferir")
def transferir(request: Request, rid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    """Uma transação: titular atual -> 'transferido' (histórico); residente -> novo titular PENDENTE de aprovação da
    administração; os dois trocam de lugar na lista de residentes."""
    m = morador_atual(request, db, sessao)
    r = _residente_do_apto(db, rid, m)
    agora, ip = datetime.now(timezone.utc), ip_de(request)
    m.status, m.decidido_em, m.decidido_por, m.decidido_ip = "transferido", agora, f"transferido para {r.nome} (CPF {r.cpf_fmt})", ip
    db.flush()  # libera o índice único do apto antes de inserir o novo titular
    novo = Morador(unidade_id=m.unidade_id, nome=r.nome, cpf=r.cpf, nascimento=r.nascimento, email=r.email, telefone=r.telefone,
                   status="pendente", origem="transferencia")
    db.add(novo)
    db.delete(r)
    db.add(Residente(unidade_id=m.unidade_id, nome=m.nome, cpf=m.cpf, nascimento=m.nascimento, email=m.email, telefone=m.telefone,
                     tipo="morador", cadastrado_por="transferência", cadastrado_ip=ip))
    db.commit()
    rot, quando = m.unidade.rotulo, agora.astimezone(FUSO).strftime("%d/%m/%Y às %H:%M")
    notificar(m.email, "[Jardim Independência] Você transferiu o acesso",
              f"Em {quando} você transferiu o acesso à área do condômino de {rot} para {novo.nome} (CPF {novo.cpf_fmt}).\n"
              f"Seu acesso a este apartamento foi encerrado. O novo acesso ficará pendente de aprovação da administração.")
    notificar(novo.email, "[Jardim Independência] Acesso transferido a você: aguardando aprovação",
              f"{m.nome} transferiu a você o acesso à área do condômino para {rot}.\n"
              f"A administração vai conferir e aprovar. Depois disso, entre em {SITE_URL}/morador/login com CPF e data de nascimento.")
    notificar(MAIL_CONTATO, f"[Site] Acesso transferido, aguardando aprovação: {novo.nome} ({rot})",
              f"{m.nome} transferiu o acesso de {rot} para {novo.nome} (CPF {novo.cpf_fmt}) em {quando}.\nRevise em {SITE_URL}/admin/moradores/{novo.id}")
    registrar("Acesso TRANSFERIDO pelo condômino (pendente)", request, unidade=rot, de=f"{m.nome} ({m.cpf_fmt})", para=f"{novo.nome} ({novo.cpf_fmt})")
    resp = render(request, "morador/transferido.html", apto=rot, novo=novo)
    resp.delete_cookie(auth.COOKIE)
    return resp
