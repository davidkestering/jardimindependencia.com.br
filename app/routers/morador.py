from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
from db import get_db
from mail import enviar
from config import MAIL_CONTATO, SITE_URL
from models import Documento, Morador, Unidade

router = APIRouter(prefix="/morador")


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


def morador_atual(request: Request, db: Session, sessao: dict) -> Morador:
    m = db.get(Morador, sessao["id"])
    if not m or m.status != "aprovado":
        raise auth.HTTPException(status_code=303, headers={"Location": "/morador/sair"})
    return m


@router.get("/login")
def login(request: Request, next: str = "/morador"):
    return render(request, "morador/login.html", next=next)


@router.post("/login")
def login_post(request: Request, cpf: str = Form(...), nascimento: str = Form(...), next: str = Form("/morador"),
               db: Session = Depends(get_db)):
    cpf_d = auth.so_digitos(cpf)
    chave = f"morador:{request.client.host}:{cpf_d}"
    if auth.bloqueado(chave):
        return render(request, "morador/login.html", erro="Muitas tentativas. Aguarde 15 minutos.", next=next)
    nasc = auth.parse_data(nascimento)
    m = db.scalar(select(Morador).where(Morador.cpf == cpf_d)) if auth.cpf_valido(cpf_d) and nasc else None
    if not m or m.nascimento != nasc:
        auth.registrar_tentativa(chave)
        return render(request, "morador/login.html", erro="CPF ou data de nascimento não conferem.", next=next)
    if m.status == "pendente":
        return render(request, "morador/login.html", erro="Seu cadastro ainda aguarda aprovação da administração.", next=next)
    if m.status == "bloqueado":
        return render(request, "morador/login.html", erro="Acesso bloqueado. Procure a administração.", next=next)
    auth.limpar_tentativas(chave)
    resp = RedirectResponse(next if next.startswith("/") else "/morador", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.criar_sessao("morador", str(m.id)), httponly=True, secure=True, samesite="lax",
                    max_age=auth.SESSAO_HORAS * 3600)
    return resp


@router.get("/sair")
def sair():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.get("/cadastro")
def cadastro(request: Request, db: Session = Depends(get_db)):
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != ""))})
    return render(request, "morador/cadastro.html", blocos=blocos)


@router.post("/cadastro")
def cadastro_post(request: Request, nome: str = Form(...), cpf: str = Form(...), nascimento: str = Form(...),
                  bloco: str = Form(...), apto: str = Form(...), email: str = Form(""), telefone: str = Form(""),
                  db: Session = Depends(get_db)):
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != ""))})
    cpf_d, nasc = auth.so_digitos(cpf), auth.parse_data(nascimento)
    apto = auth.so_digitos(apto).zfill(3)[-3:]
    erro = None
    if not auth.cpf_valido(cpf_d):
        erro = "CPF inválido."
    elif not nasc or nasc > date.today():
        erro = "Data de nascimento inválida."
    elif not (u := db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == apto, Unidade.ativa))):
        erro = f"Unidade bloco {bloco} apto {apto} não encontrada."
    elif db.scalar(select(Morador).where(Morador.cpf == cpf_d)):
        erro = "Já existe um cadastro com este CPF. Se ainda não foi aprovado, aguarde a administração."
    if erro:
        return render(request, "morador/cadastro.html", erro=erro, blocos=blocos, form=locals())
    m = Morador(unidade_id=u.id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc,
                email=email.strip()[:160] or None, telefone=telefone.strip()[:20] or None, status="pendente")
    db.add(m)
    db.commit()
    enviar(MAIL_CONTATO, f"[Site] Novo cadastro pendente: {m.nome} ({u.rotulo})",
           f"Morador {m.nome} solicitou acesso para {u.rotulo}.\nAprove em {SITE_URL}/admin/moradores?status=pendente")
    return render(request, "morador/cadastro.html", sucesso=True, blocos=blocos)


@router.get("")
def painel(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    docs = db.scalars(select(Documento).where(Documento.publico).order_by(Documento.criado_em.desc())).all()
    return render(request, "morador/painel.html", morador=m, documentos=docs)


@router.get("/documentos/{doc_id}")
def baixar(doc_id: str, request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    from routers.arquivos import servir_documento
    morador_atual(request, db, sessao)
    return servir_documento(db, doc_id, apenas_publicos=True)
