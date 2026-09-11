"""Comunicados da administração: rascunho (só admin) | condominos (área logada) | publico (site)."""
import threading
import uuid
from datetime import datetime, timezone
from itertools import groupby

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
import interfone
from config import SITE_URL
from db import SessionLocal, get_db
from mail import enviar, registrar
from models import AdminUser, Comunicado, Morador
from routers.admin import admin_dep
from routers.morador import morador_atual

router = APIRouter()
VISIBILIDADES = ["rascunho", "condominos", "publico"]
MESES = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
RESUMO_N = 200


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "resumo": resumo, **ctx})


def resumo(texto: str, n: int = RESUMO_N) -> str:
    t = " ".join(texto.split())
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0] + "…"


def visiveis(db: Session, niveis: tuple[str, ...], limite: int | None = None):
    stmt = select(Comunicado).where(Comunicado.visibilidade.in_(niveis)).order_by(Comunicado.publicado_em.desc())
    return db.scalars(stmt.limit(limite) if limite else stmt).all()


def publicos(db: Session, limite: int | None = None):
    return visiveis(db, ("publico",), limite)


def novos_para(db: Session, m: Morador) -> list[Comunicado]:
    lista = visiveis(db, ("condominos", "publico"))
    return [c for c in lista if not m.comunicados_vistos_em or c.publicado_em > m.comunicados_vistos_em]


def por_mes(lista):
    return [(f"{MESES[mes]} de {ano}", list(g)) for (ano, mes), g in groupby(lista, key=lambda c: (c.publicado_em.year, c.publicado_em.month))]


def notificar_comunicado(cid: uuid.UUID) -> None:
    """E-mail a cada condômino aprovado (1 por endereço) e push a quem ativou. Em thread; roda só na 1ª publicação."""
    def corpo():
        with SessionLocal() as db:
            c = db.get(Comunicado, cid)
            emails = sorted({m.email.lower() for m in db.scalars(select(Morador).where(Morador.status == "aprovado"))})
            assunto = f"[Jardim Independência] Comunicado: {c.titulo}"
            texto = f"{c.titulo}\n\n{c.texto}\n\nVeja na sua área: {SITE_URL}/morador/comunicados/{c.id}"
            payload = {"titulo": f"Comunicado: {c.titulo}", "corpo": resumo(c.texto, 100), "url": f"/morador/comunicados/{c.id}", "tag": "comunicado"}
        for e in emails:
            enviar(e, assunto, texto)
        interfone.push_para_todos(payload)
    threading.Thread(target=corpo, daemon=True).start()


# ---------- administração ----------
@router.get("/admin/comunicados")
def admin_lista(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    lista = db.scalars(select(Comunicado).order_by(Comunicado.criado_em.desc())).all()
    return render(request, "admin/comunicados.html", comunicados=lista, visibilidades=VISIBILIDADES)


def _validar(titulo: str, texto: str) -> tuple[str, str]:
    titulo, texto = titulo.strip()[:200], texto.strip()[:20000]
    if not titulo or not texto:
        raise HTTPException(400, "Título e texto são obrigatórios")
    return titulo, texto


def _mudar_visibilidade(request: Request, c: Comunicado, vis: str, admin: AdminUser, db: Session) -> None:
    if vis not in VISIBILIDADES:
        raise HTTPException(400, "Visibilidade inválida")
    primeira = vis != "rascunho" and c.publicado_em is None
    c.visibilidade = vis
    if primeira:
        c.publicado_em = datetime.now(timezone.utc)
    db.commit()
    registrar(f"Comunicado {vis}", request, admin=admin.login, titulo=c.titulo, notificado="sim" if primeira else "não")
    if primeira:
        notificar_comunicado(c.id)


@router.post("/admin/comunicados")
def admin_criar(request: Request, titulo: str = Form(...), texto: str = Form(...), visibilidade: str = Form("rascunho"),
                admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    titulo, texto = _validar(titulo, texto)
    c = Comunicado(titulo=titulo, texto=texto, autor=admin.login)
    db.add(c)
    db.commit()
    _mudar_visibilidade(request, c, visibilidade, admin, db)
    return RedirectResponse("/admin/comunicados", status_code=303)


@router.get("/admin/comunicados/{cid}")
def admin_editar(request: Request, cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    return render(request, "admin/comunicado.html", c=c, visibilidades=VISIBILIDADES)


@router.post("/admin/comunicados/{cid}")
def admin_salvar(request: Request, cid: uuid.UUID, titulo: str = Form(...), texto: str = Form(...),
                 admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    c.titulo, c.texto = _validar(titulo, texto)
    db.commit()
    return RedirectResponse(f"/admin/comunicados/{c.id}", status_code=303)


@router.post("/admin/comunicados/{cid}/visibilidade")
def admin_visibilidade(request: Request, cid: uuid.UUID, visibilidade: str = Form(...),
                       admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid) or (_ for _ in ()).throw(HTTPException(404))
    _mudar_visibilidade(request, c, visibilidade, admin, db)
    return RedirectResponse("/admin/comunicados", status_code=303)


@router.post("/admin/comunicados/{cid}/excluir")
def admin_excluir(cid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid)
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse("/admin/comunicados", status_code=303)


# ---------- condômino ----------
@router.get("/morador/comunicados")
def morador_lista(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    novos = {c.id for c in novos_para(db, m)}
    lista = visiveis(db, ("condominos", "publico"))
    m.comunicados_vistos_em = datetime.now(timezone.utc)
    db.commit()
    return render(request, "morador/comunicados.html", morador=m, grupos=por_mes(lista), novos_ids=novos)


@router.get("/morador/comunicados/{cid}")
def morador_ver(request: Request, cid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    c = db.get(Comunicado, cid)
    if not c or c.visibilidade == "rascunho":
        raise HTTPException(404)
    return render(request, "morador/comunicado.html", morador=m, c=c)


# ---------- site ----------
@router.get("/comunicados")
def site_lista(request: Request, db: Session = Depends(get_db)):
    return render(request, "site/comunicados.html", grupos=por_mes(publicos(db)))


@router.get("/comunicados/{cid}")
def site_ver(request: Request, cid: uuid.UUID, db: Session = Depends(get_db)):
    c = db.get(Comunicado, cid)
    if not c or c.visibilidade != "publico":
        raise HTTPException(404)
    return render(request, "site/comunicado.html", c=c)


if __name__ == "__main__":
    assert resumo("a b c", 10) == "a b c"
    assert resumo("palavra " * 50, 20) == "palavra palavra…"
    print("comunicados ok")
