"""Fale Conosco: o condômino manda sugestões, reclamações, ideias, conselhos e elogios à administração, com
imagens e um vídeo opcionais. NÃO é ocorrência: não tem número, resposta nem finalização; a administração só lê, vendo quem
enviou. Imutável: sem edição nem exclusão. E-mails: envio -> contato@ + cópia ao condômino.
Anexos em UPLOAD_DIR/arquivos_fale_conosco: BL_XX_AP_XXX_fale_DDMMYYYY_HHMMSS_<sequencial do apartamento>.<extensão>."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from mimetypes import guess_type
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

import auth
from config import MAIL_CONTATO, SITE_URL, UPLOAD_DIR
from db import get_db
from mail import FUSO, ip_de, notificar, registrar
from models import AdminUser, FaleConosco, FaleConoscoAnexo, Morador
from termo import TERMO_FALE_CONOSCO
from routers.admin import ERRO_UPLOAD, EXT_VIDEO, MAX_TOTAL_MB, MAX_VIDEO_S, _gravar_em_blocos, admin_dep
from routers.garagem import voltar
from routers.morador import morador_atual

router = APIRouter()
PASTA_ARQUIVOS = "arquivos_fale_conosco"
PASTA = Path(UPLOAD_DIR) / PASTA_ARQUIVOS
TIPOS = {"sugestao": "Sugestão", "reclamacao": "Reclamação", "ideia": "Ideia", "conselho": "Conselho", "elogio": "Elogio"}
SITUACOES = {"": "Lidas e não lidas", "nao_lidas": "Não lidas", "lidas": "Lidas"}
EXT_IMAGEM = {".jpg", ".jpeg", ".png"}
MAX_IMAGENS = 5
ASSUNTO = "[Jardim Independência] Fale Conosco — {tipo} · {unidade}"


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "tipos": TIPOS, **ctx})


def volta(erro: str = "", ok: str = "") -> RedirectResponse:
    return voltar(erro, ok, para="/morador/fale-conosco")


def anexos_invalidos(arquivos: list[UploadFile]) -> str | None:
    """Mensagem de erro do conjunto de anexos (tipos e quantidades), ou None. O conteúdo é conferido ao gravar."""
    extensoes = [Path(a.filename).suffix.lower() for a in arquivos]
    if any(e not in EXT_IMAGEM | EXT_VIDEO for e in extensoes):
        return "Anexos: envie apenas imagens JPG ou PNG e vídeo MP4 ou MOV."
    if sum(e in EXT_IMAGEM for e in extensoes) > MAX_IMAGENS:
        return f"Anexos: envie no máximo {MAX_IMAGENS} imagens."
    if sum(e in EXT_VIDEO for e in extensoes) > 1:
        return "Anexos: envie no máximo 1 vídeo."
    return None


def dia(texto: str) -> date | None:
    """Data AAAA-MM-DD do filtro de período; None se vazia ou inválida."""
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


def apagar(arquivos: list[Path]) -> None:
    for p in arquivos:
        p.unlink(missing_ok=True)


async def gravar_provisorios(arquivos: list[UploadFile]) -> tuple[list[tuple[Path, str]], str | None]:
    """Grava e verifica os anexos (assinatura, duração do vídeo, antivírus) com nome provisório: o definitivo depende do
    sequencial do apartamento. Devolve ([(arquivo, nome original)], erro); com erro, nada fica no disco."""
    restante, gravados = MAX_TOTAL_MB * 1024 * 1024, []
    try:
        for a in arquivos:
            provisorio = PASTA / f".envio_{uuid.uuid4().hex}{Path(a.filename).suffix.lower()}"
            gravados.append((provisorio, Path(a.filename).name[:255]))
            n = await _gravar_em_blocos(a, provisorio, restante)
            if n < 0:
                apagar([p for p, _ in gravados])
                return [], ERRO_UPLOAD[n].format(mb=MAX_TOTAL_MB, nome=a.filename)
            restante -= n
    except BaseException:
        apagar([p for p, _ in gravados])
        raise
    return gravados, None


def servir(db: Session, aid: uuid.UUID, morador: Morador | None) -> FileResponse:
    a = db.get(FaleConoscoAnexo, aid)
    if not a or (morador is not None and db.get(FaleConosco, a.mensagem_id).morador_id != morador.id):  # condômino só vê os anexos que enviou
        raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / a.arquivo).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    # abre na própria página (imagem e vídeo); o tipo vem da extensão conferida no envio, nunca do que o navegador adivinhar
    return FileResponse(caminho, filename=a.nome_original, media_type=guess_type(caminho.name)[0], content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# ---------- condômino ----------
@router.get("/morador/fale-conosco")
def pagina(request: Request, erro: str = "", ok: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    lista = db.scalars(select(FaleConosco).where(FaleConosco.morador_id == m.id).order_by(FaleConosco.criado_em.desc())
                       .options(selectinload(FaleConosco.anexos))).all()
    return render(request, "morador/fale_conosco.html", morador=m, mensagens=lista, erro=erro, ok=ok, captcha=auth.captcha_novo(),
                  max_mb=MAX_TOTAL_MB, max_video_s=MAX_VIDEO_S, max_imagens=MAX_IMAGENS)


@router.post("/morador/fale-conosco")
async def enviar(request: Request, tipo: str = Form(""), texto: str = Form(""), arquivos: list[UploadFile] = File([]),
                 declaracao: str = Form(""), captcha: str = Form(""), captcha_token: str = Form(""),
                 sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u, texto = m.unidade, texto.strip()[:10000]
    if not auth.captcha_ok(captcha_token, captcha):
        registrar("Fale Conosco RECUSADO (captcha)", request, condomino=m.nome, unidade=u.rotulo, motivo=auth.captcha_falha(captcha_token, captcha))
        return volta("Resposta da conta de verificação incorreta. Tente novamente.")
    if not declaracao:
        return volta("É preciso aceitar a declaração de responsabilidade.")
    if tipo not in TIPOS:
        return volta("Escolha o tipo da mensagem.")
    if not texto:
        return volta("Escreva a mensagem.")
    arquivos = [a for a in arquivos if a and a.filename]
    if erro := anexos_invalidos(arquivos):
        return volta(erro)
    gravados, erro = await gravar_provisorios(arquivos)
    if erro:
        return volta(erro)
    agora, ip = datetime.now(timezone.utc), ip_de(request)
    no_disco = [p for p, _ in gravados]
    try:
        # daqui até o commit não há await: dois envios simultâneos do apartamento não pegam o mesmo número (e a chave única barra o resto)
        f = FaleConosco(unidade_id=u.id, morador_id=m.id, tipo=tipo, texto=texto, criado_ip=ip, termo_texto=TERMO_FALE_CONOSCO, termo_aceito_em=agora, termo_ip=ip)
        db.add(f)
        db.flush()
        numero = db.scalar(select(func.coalesce(func.max(FaleConoscoAnexo.numero), 0)).where(FaleConoscoAnexo.unidade_id == u.id))
        for i, (provisorio, original) in enumerate(gravados):
            numero += 1
            definitivo = PASTA / f"BL_{u.bloco}_AP_{u.apto}_fale_{agora.astimezone(FUSO):%d%m%Y_%H%M%S}_{numero}{provisorio.suffix}"
            no_disco[i] = provisorio.replace(definitivo)
            db.add(FaleConoscoAnexo(mensagem_id=f.id, unidade_id=u.id, numero=numero, arquivo=f"{PASTA_ARQUIVOS}/{definitivo.name}", nome_original=original))
        db.commit()
    except BaseException:
        db.rollback()
        apagar(no_disco)
        raise
    registrar("Fale Conosco: mensagem enviada", request, condomino=m.nome, unidade=u.rotulo, tipo=TIPOS[tipo], anexos=len(gravados), declaracao_aceita="sim")
    assunto = ASSUNTO.format(tipo=TIPOS[tipo], unidade=u.rotulo)
    corpo = (f"Tipo: {TIPOS[tipo]}\nUnidade: {u.rotulo}\nCondômino: {m.nome}\nEm: {agora.astimezone(FUSO):%d/%m/%Y %H:%M}\n\n{texto}"
             + "".join(f"\n- anexo: {original}" for _, original in gravados))
    notificar(MAIL_CONTATO, assunto, f"Nova mensagem no Fale Conosco (não é ocorrência: não gera número nem resposta por este canal).\n\n{corpo}\n\n"
                                     f"Ler: {SITE_URL}/admin/fale-conosco/{f.id}")
    notificar(m.email, assunto, "Cópia da mensagem que você enviou à administração pelo Fale Conosco. Ela não é ocorrência e não recebe resposta "
                                f"por este canal.\n\n{corpo}\n\nSuas mensagens: {SITE_URL}/morador/fale-conosco")
    return volta(ok="Mensagem enviada à administração. Você receberá uma cópia por e-mail.")


@router.get("/morador/fale-conosco/anexo/{aid}")
def morador_anexo(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    return servir(db, aid, morador_atual(request, db, sessao))


# ---------- administração (só leitura) ----------
@router.get("/admin/fale-conosco")
def admin_lista(request: Request, tipo: str = "", situacao: str = "", de: str = "", ate: str = "",
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    stmt = select(FaleConosco).order_by(FaleConosco.criado_em.desc())
    d1, d2 = dia(de), dia(ate)  # período do envio, em dias de Belém, inclusive nas duas pontas
    if d1:
        stmt = stmt.where(FaleConosco.criado_em >= datetime.combine(d1, time.min, FUSO))
    if d2:
        stmt = stmt.where(FaleConosco.criado_em < datetime.combine(d2 + timedelta(days=1), time.min, FUSO))
    if tipo in TIPOS:
        stmt = stmt.where(FaleConosco.tipo == tipo)
    if situacao in ("nao_lidas", "lidas"):
        stmt = stmt.where(FaleConosco.lida_em.is_(None) if situacao == "nao_lidas" else FaleConosco.lida_em.is_not(None))
    lista = db.scalars(stmt.options(joinedload(FaleConosco.unidade), joinedload(FaleConosco.morador), selectinload(FaleConosco.anexos)).limit(500)).all()
    return render(request, "admin/fale_conosco.html", mensagens=lista, tipo=tipo, situacao=situacao, situacoes=SITUACOES,
                  de=d1 or "", ate=d2 or "")


@router.get("/admin/fale-conosco/{fid}")
def admin_ver(request: Request, fid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    f = db.get(FaleConosco, fid)
    if not f:
        raise HTTPException(404)
    if not f.lida_em and not admin.teste:  # usuário de teste das lojas olha sem marcar a mensagem como lida
        f.lida_em, f.lida_por = datetime.now(timezone.utc), admin.login
        db.commit()
        registrar("Fale Conosco: mensagem lida pela administração", request, admin=admin.login, unidade=f.unidade.rotulo, condomino=f.morador.nome, tipo=TIPOS.get(f.tipo, f.tipo))
    return render(request, "admin/fale_conosco_mensagem.html", f=f)


@router.get("/admin/fale-conosco/anexo/{aid}")
def admin_anexo(aid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir(db, aid, None)
