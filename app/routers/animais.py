"""Animais de Estimação. Condômino: cadastra, edita e exclui os animais do apartamento (nome, tipo, raça e foto opcionais),
quantos houver, ou informa que a unidade não possui animais. Administração: consulta quantas e quais unidades registraram
animais (ou informaram não possuir), com filtros, e vê as fotos para identificar um animal visto solto pelo condomínio.
Fotos em UPLOAD_DIR/imagens_animais, uma por animal: BL_XX_AP_XXX_animal_<sequencial>.<extensão>."""
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

import auth
from config import UPLOAD_DIR
from db import get_db
from mail import ip_de, registrar
from models import AdminUser, Animal, Morador, Unidade
from routers.admin import ERRO_UPLOAD, _gravar_em_blocos, admin_dep
from routers.admin import render as render_admin
from routers.garagem import MSG_REPETIDO, agora, quem, voltar
from routers.morador import morador_atual, render

router = APIRouter()
TIPOS = ("Cachorro", "Gato", "Pássaro", "Peixe", "Roedor", "Réptil", "Outro")
PASTA_FOTOS = "imagens_animais"
PASTA = Path(UPLOAD_DIR) / PASTA_FOTOS
EXT_FOTO = {".jpg", ".jpeg", ".png"}
MAX_FOTO_MB = 10
ERRO_FOTO = {**ERRO_UPLOAD, -1: f"A foto passou de {MAX_FOTO_MB} MB.", -2: "Foto recusada: o arquivo não é uma imagem JPG ou PNG válida."}
SITUACOES = {"": "Com animais registrados", "sem": "Informaram não possuir", "pendente": "Ainda não informaram"}


def animais(db: Session, unidade_id) -> list[Animal]:
    return db.scalars(select(Animal).where(Animal.unidade_id == unidade_id, Animal.excluido_em.is_(None)).order_by(Animal.numero)).all()


def do_apartamento(db: Session, aid: uuid.UUID, m: Morador) -> Animal:
    """O animal, se for do apartamento do condômino e não estiver excluído; senão 404."""
    a = db.get(Animal, aid)
    if not a or a.unidade_id != m.unidade_id or a.excluido_em:
        raise HTTPException(404)
    return a


def volta(erro: str = "", ok: str = "") -> RedirectResponse:
    return voltar(erro, ok, para="/morador/animais")


def nome_foto(u: Unidade, numero: int, ext: str) -> Path:
    return PASTA / f"BL_{u.bloco}_AP_{u.apto}_animal_{numero}{ext}"


def descricao(a: Animal) -> str:
    return f"{a.nome} ({a.tipo}), raça: {a.raca or 'não informada'}, {'com' if a.foto else 'sem'} foto"


async def ler_foto(request: Request) -> tuple[UploadFile | None, str]:
    """A foto enviada e a extensão dela. Sem arquivo escolhido o campo chega vazio (texto ou arquivo sem nome, conforme o navegador)."""
    foto = (await request.form()).get("foto")
    foto = foto if isinstance(foto, UploadFile) and foto.filename else None
    return foto, Path(foto.filename).suffix.lower() if foto else ""


def invalido(db: Session, unidade_id, nome: str, tipo: str, foto: UploadFile | None, ext: str, exceto: uuid.UUID | None = None) -> str | None:
    """Mensagem de erro dos dados do formulário, ou None. exceto: o próprio animal, na edição."""
    if not nome:
        return "Informe o nome do animal."
    if tipo not in TIPOS:
        return "Escolha o tipo de animal."
    if foto and ext not in EXT_FOTO:
        return "Foto: envie uma imagem JPG ou PNG."
    if any(a.id != exceto and a.nome.lower() == nome.lower() and a.tipo == tipo for a in animais(db, unidade_id)):
        return f"{nome} ({tipo}) já está cadastrado neste apartamento."
    return None


async def gravar_provisoria(foto: UploadFile | None, ext: str) -> tuple[Path | None, str | None]:
    """Grava e valida a foto com nome provisório (o definitivo depende do número do animal). Devolve (arquivo, erro)."""
    if not foto:
        return None, None
    arquivo = PASTA / f".envio_{uuid.uuid4().hex}{ext}"
    try:
        n = await _gravar_em_blocos(foto, arquivo, MAX_FOTO_MB * 1024 * 1024)
    except BaseException:
        arquivo.unlink(missing_ok=True)
        raise
    return (arquivo, None) if n >= 0 else (None, ERRO_FOTO[n].format(mb=MAX_FOTO_MB, nome=foto.filename))


def servir_foto(a: Animal | None) -> FileResponse:
    if not a or a.excluido_em or not a.foto:
        raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / a.foto).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    return FileResponse(caminho, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# ---------- condômino ----------
@router.get("/morador/animais")
def pagina(request: Request, editar: str = "", erro: str = "", ok: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    lista = animais(db, m.unidade_id)
    return render(request, "morador/animais.html", morador=m, u=m.unidade, animais=lista, editando=next((a for a in lista if str(a.id) == editar), None),
                  tipos=TIPOS, max_mb=MAX_FOTO_MB, erro=erro, ok=ok)


@router.post("/morador/animais")
async def cadastrar(request: Request, nome: str = Form(""), tipo: str = Form(""), raca: str = Form(""),
                    sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u = m.unidade
    nome, raca = (" ".join(x.split())[:60] for x in (nome, raca))
    foto, ext = await ler_foto(request)
    if erro := invalido(db, u.id, nome, tipo, foto, ext):
        return volta(erro)
    arquivo, erro = await gravar_provisoria(foto, ext)  # depois de gravada: onde a foto está no disco (nome provisório, depois o definitivo)
    if erro:
        return volta(erro)
    rotulo = u.rotulo
    try:
        # daqui até o commit não há await: dois envios simultâneos do apartamento não pegam o mesmo número (e a chave única barra o resto)
        numero = db.scalar(select(func.coalesce(func.max(Animal.numero), 0)).where(Animal.unidade_id == u.id)) + 1
        if arquivo:
            while (definitivo := nome_foto(u, numero, ext)).exists():  # nunca sobrescreve a foto de outro animal
                numero += 1
            arquivo = arquivo.replace(definitivo)
        db.add(Animal(unidade_id=u.id, numero=numero, nome=nome, tipo=tipo, raca=raca or None, foto=f"{PASTA_FOTOS}/{arquivo.name}" if arquivo else None,
                      cadastrado_por=quem(m), cadastrado_ip=ip_de(request)))
        u.sem_animais_em = u.sem_animais_por = None  # passou a ter animal: a declaração de que não possui deixa de valer
        db.commit()
    except BaseException as e:
        if arquivo:
            arquivo.unlink(missing_ok=True)
        if not isinstance(e, IntegrityError):
            raise
        db.rollback()
        return volta(MSG_REPETIDO)
    registrar("Animal de estimação cadastrado", request, condomino=m.nome, unidade=rotulo, animal=f"{nome} ({tipo})", raca=raca, foto=arquivo.name if arquivo else "sem foto")
    return volta(ok=f"{nome} cadastrado.")


@router.post("/morador/animais/nenhum")
def nenhum(request: Request, acao: str = Form(""), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    """Declaração de que a unidade não possui animais de estimação (acao=desfazer volta a "não informado")."""
    m = morador_atual(request, db, sessao)
    u = m.unidade
    if acao == "desfazer":
        u.sem_animais_em = u.sem_animais_por = None
        db.commit()
        registrar("Animais de estimação: declaração de que a unidade não possui desfeita", request, condomino=m.nome, unidade=u.rotulo)
        return volta(ok="Informação desfeita.")
    if animais(db, u.id):
        return volta("Há animais cadastrados neste apartamento. Exclua-os antes de informar que a unidade não possui animais.")
    u.sem_animais_em, u.sem_animais_por = agora(), quem(m)
    db.commit()
    registrar("Animais de estimação: unidade informou que não possui", request, condomino=m.nome, unidade=u.rotulo)
    return volta()  # a própria página passa a mostrar a declaração, com quem informou e quando


@router.post("/morador/animais/{aid}/editar")
async def editar(request: Request, aid: uuid.UUID, nome: str = Form(""), tipo: str = Form(""), raca: str = Form(""), sem_foto: str = Form(""),
                 sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    """Altera nome, tipo e raça; a foto pode ser trocada (a nova toma o lugar da antiga: uma só por animal) ou excluída."""
    m = morador_atual(request, db, sessao)
    a = do_apartamento(db, aid, m)

    def falha(erro: str) -> RedirectResponse:  # volta ao formulário de edição
        return RedirectResponse(f"/morador/animais?editar={aid}&erro={quote(erro)}#form", status_code=303)
    nome, raca = (" ".join(x.split())[:60] for x in (nome, raca))
    foto, ext = await ler_foto(request)
    if erro := invalido(db, a.unidade_id, nome, tipo, foto, ext, exceto=a.id):
        return falha(erro)
    arquivo, erro = await gravar_provisoria(foto, ext)
    if erro:
        return falha(erro)
    db.refresh(a)  # o upload pode ter demorado: parte do estado atual do registro (outra edição pode ter trocado a foto)
    if a.excluido_em:  # excluído enquanto a foto subia
        if arquivo:
            arquivo.unlink(missing_ok=True)
        raise HTTPException(404)
    antes, rotulo = descricao(a), m.unidade.rotulo
    antiga = Path(UPLOAD_DIR) / a.foto if a.foto else None
    nova = nome_foto(m.unidade, a.numero, ext) if arquivo else None
    try:
        a.nome, a.tipo, a.raca = nome, tipo, raca or None
        if nova:
            a.foto = f"{PASTA_FOTOS}/{nova.name}"
        elif sem_foto:
            a.foto = None
        a.alterado_em, a.alterado_por, a.alterado_ip = agora(), quem(m), ip_de(request)
        depois = descricao(a)
        db.commit()
    except BaseException as e:
        if arquivo:
            arquivo.unlink(missing_ok=True)
        if not isinstance(e, IntegrityError):
            raise
        db.rollback()
        return falha(MSG_REPETIDO)
    if antiga and (nova or sem_foto):  # registro gravado: só agora mexe nos arquivos
        antiga.unlink(missing_ok=True)
    if nova:
        arquivo.replace(nova)
    registrar("Animal de estimação alterado", request, condomino=m.nome, unidade=rotulo, antes=antes, depois=depois)
    return volta(ok=f"{nome} alterado.")


@router.post("/morador/animais/{aid}/excluir")
def excluir(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    a = do_apartamento(db, aid, m)
    a.excluido_em, a.excluido_por, a.excluido_ip = agora(), quem(m), ip_de(request)
    db.commit()
    registrar("Animal de estimação excluído", request, condomino=m.nome, unidade=m.unidade.rotulo, animal=f"{a.nome} ({a.tipo})")
    return volta(ok=f"{a.nome} excluído.")


@router.get("/morador/animais/{aid}/foto")
def foto_morador(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    return servir_foto(do_apartamento(db, aid, morador_atual(request, db, sessao)))  # só os animais do próprio apartamento


# ---------- administração ----------
@router.get("/admin/animais")
def admin_pagina(request: Request, situacao: str = "", bloco: str = "", tipo: str = "", q: str = "",
                 admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    aptos = db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.visivel).order_by(Unidade.bloco, Unidade.apto)).all()
    da_unidade = {}
    for a in db.scalars(select(Animal).where(Animal.excluido_em.is_(None)).order_by(Animal.numero)):
        da_unidade.setdefault(a.unidade_id, []).append(a)
    sem = [u for u in aptos if u.em_uso and u.sem_animais_em and u.id not in da_unidade]
    pendentes = [u for u in aptos if u.em_uso and not u.sem_animais_em and u.id not in da_unidade]
    situacao, termo = situacao if situacao in SITUACOES else "", q.strip().lower()
    unidades, outras = {}, []  # unidade -> animais dela que passam no filtro · unidades sem animais (situação "sem" ou "pendente")
    if situacao:
        outras = [u for u in (sem if situacao == "sem" else pendentes) if not bloco or u.bloco == bloco]
    else:
        for u in aptos:
            achados = [a for a in da_unidade.get(u.id, []) if (not bloco or u.bloco == bloco) and (not tipo or a.tipo == tipo)
                       and (not termo or termo in a.nome.lower() or termo in (a.raca or "").lower())]
            if achados:
                unidades[u] = achados
    return render_admin(request, "admin/animais.html", unidades=unidades, animais=[(u, a) for u, lista in unidades.items() for a in lista], outras=outras,
                        totais=(sum(u.id in da_unidade for u in aptos), len(sem), len(pendentes)), blocos=sorted({u.bloco for u in aptos}), tipos=TIPOS, situacoes=SITUACOES,
                        situacao=situacao, bloco=bloco, tipo=tipo, q=q)


@router.get("/admin/animais/{aid}/foto")
def foto_admin(aid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir_foto(db.get(Animal, aid))
