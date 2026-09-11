import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
from db import get_db
from mail import ip_de, notificar, registrar
from config import MAIL_CONTATO, SITE_URL
from models import OCUPA_APTO, Documento, Morador, Unidade

router = APIRouter(prefix="/morador")

MENSAGEM_STATUS = {
    "pendente": "Seu cadastro ainda aguarda aprovação da administração.",
    "negado": "Acesso não autorizado. Procure a administração ou solicite novo cadastro.",
}


def ocupante(db: Session, unidade_id) -> Morador | None:
    """Quem ocupa o apartamento (pendente ou aprovado). Só pode haver um."""
    return db.scalar(select(Morador).where(Morador.unidade_id == unidade_id, Morador.status.in_(OCUPA_APTO)))


def msg_ocupado(u: Unidade, m: Morador) -> str:
    extra = " (aguardando aprovação)" if m.status == "pendente" else ""
    return f"O acesso ao {u.rotulo} já foi registrado em nome de {m.nome}{extra}."


def validar_contato(email: str, telefone: str) -> str | None:
    if "@" not in email or len(email.strip()) < 5:
        return "E-mail inválido."
    if len(auth.so_digitos(telefone)) < 10:
        return "Telefone inválido: informe DDD e número."
    return None


def cookie_sessao(resp, m: Morador):
    resp.set_cookie(auth.COOKIE, auth.criar_sessao("morador", str(m.id)), httponly=True, secure=True, samesite="lax",
                    max_age=auth.SESSAO_HORAS * 3600)
    return resp


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "captcha": auth.captcha_novo(), **ctx})


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
               captcha: str = Form(""), captcha_token: str = Form(""), db: Session = Depends(get_db)):
    cpf_d = auth.so_digitos(cpf)
    chave = f"morador:{ip_de(request)}:{cpf_d}"
    if auth.bloqueado(chave):
        return render(request, "morador/login.html", erro="Muitas tentativas. Aguarde 15 minutos.", next=next)
    if not auth.captcha_ok(captcha_token, captcha):
        return render(request, "morador/login.html", erro="Resposta da conta de verificação incorreta. Tente novamente.", next=next)
    nasc = auth.parse_data(nascimento)
    # O mesmo CPF pode ter mais de um apartamento: uma linha por unidade.
    ms = db.scalars(select(Morador).join(Unidade).where(Morador.cpf == cpf_d, Morador.nascimento == nasc)
                    .order_by(Unidade.bloco, Unidade.apto)).all() if auth.cpf_valido(cpf_d) and nasc else []
    if not ms:
        auth.registrar_tentativa(chave)
        registrar("Login CONDÔMINO recusado", request, cpf=cpf, nascimento=nascimento, motivo="CPF ou data não conferem")
        return render(request, "morador/login.html", erro="CPF ou data de nascimento não conferem.", next=next)
    aprovados = [x for x in ms if x.status == "aprovado"]
    m = aprovados[0] if aprovados else None
    if not m:
        pior = min(ms, key=lambda x: list(MENSAGEM_STATUS).index(x.status))
        registrar(f"Login CONDÔMINO recusado ({pior.status})", request, nome=pior.nome, unidade=pior.unidade.rotulo, cpf=cpf, nascimento=nascimento)
        return render(request, "morador/login.html", erro=MENSAGEM_STATUS[pior.status], next=next)
    auth.limpar_tentativas(chave)
    registrar("Login CONDÔMINO realizado", request, nome=m.nome, unidade=m.unidade.rotulo, cpf=cpf, nascimento=nascimento)
    return cookie_sessao(RedirectResponse(next if next.startswith("/") else "/morador", status_code=303), m)


@router.get("/trocar/{mid}")
def trocar(mid: uuid.UUID, request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    """Troca a unidade da sessão para outro apartamento aprovado do mesmo CPF."""
    atual = morador_atual(request, db, sessao)
    alvo = db.get(Morador, mid)
    if not alvo or alvo.cpf != atual.cpf or alvo.status != "aprovado":
        raise HTTPException(403, "Unidade não pertence a este CPF")
    return cookie_sessao(RedirectResponse("/morador", status_code=303), alvo)


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
                  bloco: str = Form(...), apto: str = Form(...), email: str = Form(...), telefone: str = Form(...),
                  declaracao: str = Form(""), captcha: str = Form(""), captcha_token: str = Form(""),
                  db: Session = Depends(get_db)):
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != ""))})
    cpf_d, nasc = auth.so_digitos(cpf), auth.parse_data(nascimento)
    apto = auth.so_digitos(apto).zfill(3)[-3:]
    dados = dict(nome=nome, cpf=cpf, nascimento=nascimento, bloco=bloco, apto=apto, email=email, telefone=telefone)
    erro = None
    if not auth.captcha_ok(captcha_token, captcha):
        erro = "Resposta da conta de verificação incorreta. Tente novamente."
    elif not declaracao:
        erro = "É preciso aceitar a declaração de veracidade das informações."
    elif not auth.cpf_valido(cpf_d):
        erro = "CPF inválido."
    elif not nasc or nasc > date.today():
        erro = "Data de nascimento inválida."
    elif erro_contato := validar_contato(email, telefone):
        erro = erro_contato
    elif not (u := db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == apto, Unidade.ativa))):
        erro = f"Unidade bloco {bloco} apto {apto} não encontrada."
    elif ocup := ocupante(db, u.id):
        erro = msg_ocupado(u, ocup)
    if erro:
        registrar("Cadastro no site RECUSADO", request, motivo=erro, **dados)
        return render(request, "morador/cadastro.html", erro=erro, blocos=blocos, form=dados)
    m = Morador(unidade_id=u.id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc,
                email=email.strip()[:160], telefone=telefone.strip()[:20], status="pendente")
    db.add(m)
    try:
        db.commit()
    except IntegrityError:  # dois envios simultâneos para o mesmo apto: o índice único parcial segura o segundo
        db.rollback()
        erro = msg_ocupado(u, ocupante(db, u.id))
        registrar("Cadastro no site RECUSADO", request, motivo=erro, **dados)
        return render(request, "morador/cadastro.html", erro=erro, blocos=blocos, form=dados)
    registrar("Cadastro no site (pendente)", request, declaracao_aceita="sim", **dados)
    notificar(MAIL_CONTATO, f"[Site] Novo cadastro pendente: {m.nome} ({u.rotulo})",
              f"Morador {m.nome} solicitou acesso para {u.rotulo}.\nRevise em {SITE_URL}/admin/moradores/{m.id}")
    return render(request, "morador/cadastro.html", sucesso=True, blocos=blocos)


@router.get("")
def painel(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    docs = db.scalars(select(Documento).where(Documento.publico).order_by(Documento.criado_em.desc())).all()
    outras = db.scalars(select(Morador).join(Unidade).where(Morador.cpf == m.cpf, Morador.status == "aprovado", Morador.id != m.id)
                        .order_by(Unidade.bloco, Unidade.apto)).all()
    from routers.comunicados import novos_para, resumo
    novos = novos_para(db, m)
    return render(request, "morador/painel.html", morador=m, documentos=docs, outras=outras, novos=novos, resumo=resumo)


@router.get("/documentos/{doc_id}")
def baixar(doc_id: str, request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    from routers.arquivos import servir_documento
    morador_atual(request, db, sessao)
    return servir_documento(db, doc_id, apenas_publicos=True)
