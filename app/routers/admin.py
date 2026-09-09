import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import auth
from config import UPLOAD_DIR
from db import get_db
from models import AdminUser, Documento, Morador, Unidade
from routers.arquivos import servir_documento

router = APIRouter(prefix="/admin")
CATEGORIAS = ["Convenção", "Regimento interno", "Atas de assembleia", "Balancetes", "Comunicados", "Outros"]
EXT_OK = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_MB = 25


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


def admin_dep(sessao: dict = Depends(auth.exigir("admin")), db: Session = Depends(get_db)) -> AdminUser:
    a = db.get(AdminUser, sessao["id"])
    if not a:
        raise HTTPException(status_code=303, headers={"Location": "/admin/sair"})
    return a


@router.get("/login")
def login(request: Request, next: str = "/admin"):
    return render(request, "admin/login.html", next=next)


@router.post("/login")
def login_post(request: Request, login: str = Form(...), senha: str = Form(...), next: str = Form("/admin"),
               db: Session = Depends(get_db)):
    chave = f"admin:{request.client.host}"
    if auth.bloqueado(chave):
        return render(request, "admin/login.html", erro="Muitas tentativas. Aguarde 15 minutos.", next=next)
    a = db.scalar(select(AdminUser).where(AdminUser.login == login.strip().lower()))
    if not a or not auth.verificar_senha(senha, a.senha_hash):
        auth.registrar_tentativa(chave)
        return render(request, "admin/login.html", erro="Login ou senha incorretos.", next=next)
    auth.limpar_tentativas(chave)
    resp = RedirectResponse(next if next.startswith("/") else "/admin", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.criar_sessao("admin", str(a.id)), httponly=True, secure=True, samesite="lax",
                    max_age=auth.SESSAO_HORAS * 3600)
    return resp


@router.get("/sair")
def sair():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.get("")
def painel(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    contagem = {s: n for s, n in db.execute(select(Morador.status, func.count()).group_by(Morador.status))}
    return render(request, "admin/painel.html", admin=admin, contagem=contagem,
                  unidades=db.scalar(select(func.count()).select_from(Unidade).where(Unidade.ativa)),
                  documentos=db.scalar(select(func.count()).select_from(Documento)))


# ---- moradores ----
@router.get("/moradores")
def moradores(request: Request, status: str = "", q: str = "", admin: AdminUser = Depends(admin_dep),
              db: Session = Depends(get_db)):
    stmt = select(Morador).join(Unidade).order_by(Unidade.bloco, Unidade.apto, Morador.nome)
    if status:
        stmt = stmt.where(Morador.status == status)
    if q:
        stmt = stmt.where(Morador.nome.ilike(f"%{q}%") | Morador.cpf.contains(auth.so_digitos(q) or "§"))
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != ""))}) + ["PORTARIA", "ADMINISTRACAO"]
    return render(request, "admin/moradores.html", moradores=db.scalars(stmt).all(), status=status, q=q, blocos=blocos)


@router.post("/moradores")
def morador_criar(request: Request, nome: str = Form(...), cpf: str = Form(...), nascimento: str = Form(...),
                  bloco: str = Form(...), apto: str = Form(...), email: str = Form(""), telefone: str = Form(""),
                  admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    cpf_d, nasc = auth.so_digitos(cpf), auth.parse_data(nascimento)
    apto = "" if bloco in ("PORTARIA", "ADMINISTRACAO") else auth.so_digitos(apto).zfill(3)[-3:]
    u = db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == apto))
    if not auth.cpf_valido(cpf_d) or not nasc or not u:
        raise HTTPException(400, "CPF, data ou unidade inválidos")
    if db.scalar(select(Morador).where(Morador.cpf == cpf_d)):
        raise HTTPException(400, "CPF já cadastrado")
    db.add(Morador(unidade_id=u.id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc, status="aprovado",
                   email=email.strip()[:160] or None, telefone=telefone.strip()[:20] or None))
    db.commit()
    return RedirectResponse("/admin/moradores", status_code=303)


@router.post("/moradores/{mid}/status")
def morador_status(mid: uuid.UUID, status: str = Form(...), admin: AdminUser = Depends(admin_dep),
                   db: Session = Depends(get_db)):
    if status not in ("aprovado", "bloqueado", "pendente"):
        raise HTTPException(400)
    m = db.get(Morador, mid) or (_ for _ in ()).throw(HTTPException(404))
    m.status = status
    db.commit()
    return RedirectResponse("/admin/moradores?status=pendente" if status == "aprovado" else "/admin/moradores", status_code=303)


@router.post("/moradores/{mid}/excluir")
def morador_excluir(mid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    m = db.get(Morador, mid)
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse("/admin/moradores", status_code=303)


# ---- documentos ----
@router.get("/documentos")
def documentos(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    docs = db.scalars(select(Documento).order_by(Documento.criado_em.desc())).all()
    return render(request, "admin/documentos.html", documentos=docs, categorias=CATEGORIAS)


@router.post("/documentos")
async def documento_enviar(request: Request, titulo: str = Form(...), categoria: str = Form(...),
                           publico: str = Form(""), arquivo: UploadFile = None,
                           admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    ext = Path(arquivo.filename or "").suffix.lower()
    if ext not in EXT_OK:
        raise HTTPException(400, "Envie PDF, JPG ou PNG")
    dados = await arquivo.read()
    if len(dados) > MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"Arquivo maior que {MAX_MB} MB")
    nome = f"documentos/{uuid.uuid4()}{ext}"
    destino = Path(UPLOAD_DIR) / nome
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    db.add(Documento(titulo=titulo.strip()[:200], categoria=categoria if categoria in CATEGORIAS else "Outros",
                     arquivo=nome, nome_original=Path(arquivo.filename).name[:255], publico=bool(publico)))
    db.commit()
    return RedirectResponse("/admin/documentos", status_code=303)


@router.post("/documentos/{did}/publico")
def documento_publico(did: uuid.UUID, publico: str = Form(""), admin: AdminUser = Depends(admin_dep),
                      db: Session = Depends(get_db)):
    d = db.get(Documento, did) or (_ for _ in ()).throw(HTTPException(404))
    d.publico = publico == "1"
    db.commit()
    return RedirectResponse("/admin/documentos", status_code=303)


@router.post("/documentos/{did}/excluir")
def documento_excluir(did: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    d = db.get(Documento, did)
    if d:
        (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True)
        db.delete(d)
        db.commit()
    return RedirectResponse("/admin/documentos", status_code=303)


@router.get("/documentos/{did}")
def documento_baixar(did: str, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir_documento(db, did, apenas_publicos=False)


# ---- senha do admin ----
@router.post("/senha")
def trocar_senha(request: Request, atual: str = Form(...), nova: str = Form(...), admin: AdminUser = Depends(admin_dep),
                 db: Session = Depends(get_db)):
    if not auth.verificar_senha(atual, admin.senha_hash) or len(nova) < 8:
        raise HTTPException(400, "Senha atual incorreta ou nova senha com menos de 8 caracteres")
    admin.senha_hash = auth.hash_senha(nova)
    db.commit()
    return RedirectResponse("/admin", status_code=303)
