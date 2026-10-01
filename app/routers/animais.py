"""Animais de Estimação. Condômino: cadastra os animais do apartamento (nome, tipo, raça e foto opcionais), quantos houver.
Administração: consulta quantas e quais unidades registraram animais, com filtros, e vê as fotos para identificar um animal
visto solto pelo condomínio. Fotos em UPLOAD_DIR/imagens_animais, uma por animal: BL_XX_AP_XXX_animal_<sequencial>.<extensão>."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.datastructures import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
from config import UPLOAD_DIR
from db import get_db
from mail import ip_de, registrar
from models import AdminUser, Animal, Unidade
from routers.admin import ERRO_UPLOAD, _gravar_em_blocos, admin_dep
from routers.admin import render as render_admin
from routers.garagem import MSG_REPETIDO, agora, quem, voltar
from routers.morador import morador_atual, render

router = APIRouter()
TIPOS = ("Cachorro", "Gato", "Pássaro", "Peixe", "Roedor", "Réptil", "Outro")
PASTA_FOTOS = "imagens_animais"
EXT_FOTO = {".jpg", ".jpeg", ".png"}
MAX_FOTO_MB = 10
ERRO_FOTO = {**ERRO_UPLOAD, -1: f"A foto passou de {MAX_FOTO_MB} MB.", -2: "Foto recusada: o arquivo não é uma imagem JPG ou PNG válida."}


def animais(db: Session, unidade_id) -> list[Animal]:
    return db.scalars(select(Animal).where(Animal.unidade_id == unidade_id, Animal.excluido_em.is_(None)).order_by(Animal.numero)).all()


def volta(erro: str = "", ok: str = "") -> RedirectResponse:
    return voltar(erro, ok, para="/morador/animais")


def servir_foto(a: Animal | None) -> FileResponse:
    if not a or a.excluido_em or not a.foto:
        raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / a.foto).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    return FileResponse(caminho, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


# ---------- condômino ----------
@router.get("/morador/animais")
def pagina(request: Request, erro: str = "", ok: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    return render(request, "morador/animais.html", morador=m, u=m.unidade, animais=animais(db, m.unidade_id), tipos=TIPOS, max_mb=MAX_FOTO_MB, erro=erro, ok=ok)


@router.post("/morador/animais")
async def cadastrar(request: Request, nome: str = Form(""), tipo: str = Form(""), raca: str = Form(""),
                    sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u = m.unidade
    nome, raca = (" ".join(x.split())[:60] for x in (nome, raca))
    # foto opcional: sem arquivo escolhido o campo chega vazio (texto ou arquivo sem nome, conforme o navegador)
    foto = (await request.form()).get("foto")
    foto = foto if isinstance(foto, UploadFile) and foto.filename else None
    ext = Path(foto.filename).suffix.lower() if foto else ""
    if not nome:
        return volta("Informe o nome do animal.")
    if tipo not in TIPOS:
        return volta("Escolha o tipo de animal.")
    if foto and ext not in EXT_FOTO:
        return volta("Foto: envie uma imagem JPG ou PNG.")
    if any(a.nome.lower() == nome.lower() and a.tipo == tipo for a in animais(db, u.id)):
        return volta(f"{nome} ({tipo}) já está cadastrado neste apartamento.")
    pasta, rotulo = Path(UPLOAD_DIR) / PASTA_FOTOS, u.rotulo
    arquivo = None  # onde a foto está no disco (nome provisório, depois o definitivo): apagada se o cadastro não se concluir
    try:
        if foto:  # o nome definitivo depende do número do animal, que só é escolhido depois de a foto estar gravada e validada
            arquivo = pasta / f".envio_{uuid.uuid4().hex}{ext}"
            n = await _gravar_em_blocos(foto, arquivo, MAX_FOTO_MB * 1024 * 1024)
            if n < 0:
                return volta(ERRO_FOTO[n].format(mb=MAX_FOTO_MB, nome=foto.filename))
        # daqui até o commit não há await: dois envios simultâneos do apartamento não pegam o mesmo número (e a chave única barra o resto)
        numero = db.scalar(select(func.coalesce(func.max(Animal.numero), 0)).where(Animal.unidade_id == u.id)) + 1
        if arquivo:
            while (definitivo := pasta / f"BL_{u.bloco}_AP_{u.apto}_animal_{numero}{ext}").exists():  # nunca sobrescreve uma foto
                numero += 1
            arquivo = arquivo.replace(definitivo)
        db.add(Animal(unidade_id=u.id, numero=numero, nome=nome, tipo=tipo, raca=raca or None, foto=f"{PASTA_FOTOS}/{arquivo.name}" if arquivo else None,
                      cadastrado_por=quem(m), cadastrado_ip=ip_de(request)))
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


@router.post("/morador/animais/{aid}/excluir")
def excluir(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    a = db.get(Animal, aid)
    if not a or a.unidade_id != m.unidade_id or a.excluido_em:
        raise HTTPException(404)
    a.excluido_em, a.excluido_por, a.excluido_ip = agora(), quem(m), ip_de(request)
    db.commit()
    registrar("Animal de estimação removido", request, condomino=m.nome, unidade=m.unidade.rotulo, animal=f"{a.nome} ({a.tipo})")
    return volta(ok=f"{a.nome} removido.")


@router.get("/morador/animais/{aid}/foto")
def foto_morador(request: Request, aid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    a = db.get(Animal, aid)
    return servir_foto(a if a and a.unidade_id == m.unidade_id else None)  # só os animais do próprio apartamento


# ---------- administração ----------
@router.get("/admin/animais")
def admin_pagina(request: Request, bloco: str = "", tipo: str = "", q: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    todos = db.execute(select(Unidade, Animal).join(Animal, Animal.unidade_id == Unidade.id).where(Animal.excluido_em.is_(None))
                       .order_by(Unidade.bloco, Unidade.apto, Animal.numero)).all()
    termo = q.strip().lower()
    achados = [(u, a) for u, a in todos if (not bloco or u.bloco == bloco) and (not tipo or a.tipo == tipo)
               and (not termo or termo in a.nome.lower() or termo in (a.raca or "").lower())]
    unidades = {}  # unidade -> animais dela, na ordem de bloco e apto
    for u, a in achados:
        unidades.setdefault(u, []).append(a)
    return render_admin(request, "admin/animais.html", unidades=unidades, animais=achados, blocos=sorted({u.bloco for u, _ in todos}), tipos=TIPOS,
                        bloco=bloco, tipo=tipo, q=q)


@router.get("/admin/animais/{aid}/foto")
def foto_admin(aid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir_foto(db.get(Animal, aid))
