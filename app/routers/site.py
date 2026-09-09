from fastapi import APIRouter, Form, Request

from config import MAIL_CONTATO
from mail import enviar

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
def home(request: Request):
    return render(request, "site/home.html", fotos=FOTOS, areas=AREAS[:6])


@router.get("/condominio")
def condominio(request: Request):
    return render(request, "site/condominio.html", fotos=FOTOS, areas=AREAS)


@router.get("/galeria")
def galeria(request: Request):
    return render(request, "site/galeria.html", fotos=[{"arq": a, "leg": l, "cat": c} for a, l, c in GALERIA])


@router.get("/localizacao")
def localizacao(request: Request):
    return render(request, "site/localizacao.html")


@router.get("/contato")
def contato(request: Request):
    return render(request, "site/contato.html")


@router.post("/contato")
def contato_enviar(request: Request, nome: str = Form(...), email: str = Form(...), mensagem: str = Form(...),
                   unidade: str = Form("")):
    nome, email, mensagem = nome.strip()[:120], email.strip()[:160], mensagem.strip()[:4000]
    if not (nome and "@" in email and mensagem):
        return render(request, "site/contato.html", erro="Preencha nome, e-mail válido e mensagem.")
    corpo = f"Nome: {nome}\nE-mail: {email}\nUnidade: {unidade.strip()[:40] or '-'}\n\n{mensagem}"
    ok = enviar(MAIL_CONTATO, f"[Site] Contato de {nome}", corpo, responder_para=email)
    if not ok:
        return render(request, "site/contato.html", erro="Não foi possível enviar agora. Tente novamente em instantes.")
    return render(request, "site/contato.html", sucesso=True)
