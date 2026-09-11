from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
from config import MAIL_CONTATO
from db import get_db
from mail import enviar, ip_de, registrar
from models import Morador, Unidade

router = APIRouter()

# (arquivo, legenda, categoria) — fotos em static/img/condominio; fontes em docs/fontes-imagens.md
GALERIA = [
    ("portaria-entrada-1.jpg", "Portaria na Av. Hélio Gueiros", "Portaria"),
    ("piscina-1.jpg", "Piscina adulto", "Piscina"),
    ("fachada-bloco-1.jpg", "Fachada de bloco", "Fachadas"),
    ("quadra-esportes.jpg", "Quadra poliesportiva", "Lazer"),
    ("piscina-2.jpg", "Piscina com os blocos ao fundo", "Piscina"),
    ("pista-skate.jpg", "Pista de skate", "Lazer"),
    ("churrasqueira-quiosque.jpg", "Churrasqueira e quiosque", "Lazer"),
    ("parque-infantil.jpg", "Parque infantil", "Lazer"),
    ("rua-interna-blocos.jpg", "Rua interna entre os blocos", "Fachadas"),
    ("fachada-bloco-entrada.jpg", "Entrada de bloco", "Fachadas"),
    ("churrasqueira-salao.jpg", "Churrasqueira do salão de festas", "Lazer"),
    ("piscina-3.jpg", "Área da piscina", "Piscina"),
    ("portaria-entrada-2.jpg", "Portaria e muro frontal", "Portaria"),
    ("bloco-vista-varanda.jpg", "Bloco visto da varanda", "Fachadas"),
]
FOTOS = [(a, l) for a, l, _ in GALERIA]

AREAS = [
    ("Salão de festas", "Com churrasqueira própria, para eventos dos condôminos."),
    ("Piscina", "Área de lazer aquática com deck."),
    ("2 parques infantis", "Brinquedos em piso emborrachado."),
    ("2 churrasqueiras", "Além da churrasqueira do salão de festas."),
    ("Pista de skate", "Rampa para os jovens do condomínio."),
    ("Quadra de esportes", "Quadra poliesportiva cercada."),
    ("Loja de conveniência", "Conveniência do supermercado Matheus dentro do condomínio."),
    ("Portaria 24h", "Controle de acesso de moradores e visitantes."),
    ("Administração", "Atendimento ao condômino no próprio condomínio."),
    ("Poço artesiano", "Abastecimento próprio de água."),
    ("Zeladoria", "Manutenção e conservação das áreas comuns."),
]


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    from routers.comunicados import publicos, resumo
    return render(request, "site/home.html", fotos=FOTOS, areas=AREAS[:6], comunicados=publicos(db, 3), resumo=resumo)


@router.get("/condominio")
def condominio(request: Request):
    return render(request, "site/condominio.html", fotos=FOTOS, areas=AREAS)


@router.get("/galeria")
def galeria(request: Request):
    return render(request, "site/galeria.html", fotos=[{"arq": a, "leg": l, "cat": c} for a, l, c in GALERIA])


@router.get("/localizacao")
def localizacao(request: Request):
    return render(request, "site/localizacao.html")


def contexto_contato(request: Request, db: Session) -> dict:
    """Mapa bloco -> aptos para os selects. Condômino logado vê só as unidades aprovadas do seu CPF, já selecionadas."""
    m, sessao = None, request.state.sessao
    if sessao and sessao["t"] == "morador":
        m = db.get(Morador, sessao["id"])
        m = m if m and m.status == "aprovado" else None
    if m:
        unidades = [x.unidade for x in db.scalars(select(Morador).join(Unidade).where(Morador.cpf == m.cpf, Morador.status == "aprovado")
                                                  .order_by(Unidade.bloco, Unidade.apto))]
    else:
        unidades = db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.ativa).order_by(Unidade.bloco, Unidade.apto)).all()
    mapa: dict[str, list[str]] = {}
    for u in unidades:
        mapa.setdefault(u.bloco, []).append(u.apto)
    return {"mapa": mapa, "morador": m, "sel": (m.unidade.bloco, m.unidade.apto) if m else ("", ""), "captcha": auth.captcha_novo()}


@router.get("/doacao")
def doacao(request: Request):
    """Declaração de Doação: documento pessoal do autor, mantido só no servidor (fora do repositório)."""
    from pathlib import Path
    if not (Path(__file__).resolve().parent.parent / "templates" / "site" / "doacao.html").is_file():
        raise HTTPException(404)
    return render(request, "site/doacao.html")


@router.get("/contato")
def contato(request: Request, db: Session = Depends(get_db)):
    return render(request, "site/contato.html", **contexto_contato(request, db))


@router.post("/contato")
def contato_enviar(request: Request, nome: str = Form(...), email: str = Form(...), mensagem: str = Form(...),
                   bloco: str = Form(""), apto: str = Form(""), captcha: str = Form(""), captcha_token: str = Form(""),
                   declaracao: str = Form(""), db: Session = Depends(get_db)):
    ctx = contexto_contato(request, db)
    nome, email, mensagem = nome.strip()[:120], email.strip()[:160], mensagem.strip()[:4000]
    if not auth.captcha_ok(captcha_token, captcha):
        return render(request, "site/contato.html", erro="Resposta da conta de verificação incorreta. Tente novamente.", **ctx)
    if not declaracao:
        return render(request, "site/contato.html", erro="É preciso aceitar a declaração de responsabilidade.", **ctx)
    if not (nome and "@" in email and mensagem):
        return render(request, "site/contato.html", erro="Preencha nome, e-mail válido e mensagem.", **ctx)
    if bloco and apto not in ctx["mapa"].get(bloco, []):
        return render(request, "site/contato.html", erro="Bloco e apartamento não conferem.", **ctx)
    unidade = f"Bloco {bloco} · Apto {apto}" if bloco else "-"
    logado = f"\nCondômino logado: {m.nome} (CPF {m.cpf_fmt})" if (m := ctx["morador"]) else ""
    corpo = f"Nome: {nome}\nE-mail: {email}\nUnidade: {unidade}{logado}\nDeclaração de responsabilidade: aceita (IP {ip_de(request)})\n\n{mensagem}"
    ok = enviar(MAIL_CONTATO, f"[Site] Contato de {nome}", corpo, responder_para=email)
    registrar("Mensagem de contato enviada" if ok else "Mensagem de contato FALHOU", request, nome=nome, email=email, unidade=unidade, declaracao_aceita="sim")
    if not ok:
        return render(request, "site/contato.html", erro="Não foi possível enviar agora. Tente novamente em instantes.", **ctx)
    return render(request, "site/contato.html", sucesso=True, **ctx)
