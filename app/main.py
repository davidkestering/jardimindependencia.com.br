import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from auth import hash_senha, ler_sessao
from config import ADMIN_LOGIN, ADMIN_SENHA_INICIAL, CONDOMINIO, UPLOAD_DIR
from db import SessionLocal
from models import AdminUser, Morador, Unidade

logging.basicConfig(level=logging.INFO)
BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["condominio"] = CONDOMINIO
from termo import TERMO  # noqa: E402
templates.env.globals["termo"] = TERMO
from mail import FUSO  # noqa: E402
templates.env.filters["local"] = lambda dt: dt.astimezone(FUSO).strftime("%d/%m/%Y %H:%M") if dt else ""


def unidades_padrao():
    """Blocos 01–12: 001–004 e 101–104. Blocos 13–27: até 404. Mais PORTARIA e ADMINISTRACAO."""
    for b in range(1, 28):
        andares = 2 if b <= 12 else 5
        for andar in range(andares):
            for pos in range(1, 5):
                yield f"{b:02d}", f"{andar}{pos:02d}"
    yield "PORTARIA", ""
    yield "ADMINISTRACAO", ""


def seed():
    with SessionLocal() as db:
        if db.scalar(select(Unidade).limit(1)) is None:
            db.add_all(Unidade(bloco=b, apto=a) for b, a in unidades_padrao())
        # Usuário inicial só quando não existe nenhum (primeiro acesso); depois os mestres criam os demais.
        if ADMIN_SENHA_INICIAL and db.scalar(select(AdminUser).limit(1)) is None:
            db.add(AdminUser(login=ADMIN_LOGIN, senha_hash=hash_senha(ADMIN_SENHA_INICIAL), nome="Administração", master=True))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    seed()
    yield


app = FastAPI(title="Condomínio Jardim Independência", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.exception_handler(303)
async def redireciona(request: Request, exc):
    return RedirectResponse(exc.headers["Location"], status_code=303)


@app.middleware("http")
async def sessao_no_template(request: Request, call_next):
    s = ler_sessao(request)
    if s and s["t"] == "admin":  # menu da administração esconde o que o usuário não pode acessar
        with SessionLocal() as db:
            a = db.get(AdminUser, s["id"])
            a = a if a and not a.excluido_em else None  # desativado: sessão morre
            pend = db.scalar(select(func.count()).select_from(Morador).where(Morador.status == "pendente")) if a else 0
            s = {**s, "login": a.login, "master": bool(a.master), "areas": list(a.areas or []), "pendentes": pend} if a else None
    elif s and s["t"] == "morador":  # menu mostra o apto administrado e os outros aptos aprovados do CPF
        with SessionLocal() as db:
            m = db.get(Morador, s["id"])
            if m:
                outros = db.scalars(select(Morador).join(Unidade).where(Morador.cpf == m.cpf, Morador.status == "aprovado", Morador.id != m.id)
                                    .order_by(Unidade.bloco, Unidade.apto)).all()
                s = {**s, "login": f"{m.nome} ({m.cpf_fmt})", "apto": m.unidade.rotulo, "outros": [{"id": str(o.id), "rotulo": o.unidade.rotulo} for o in outros]}
    request.state.sessao = s
    return await call_next(request)


from routers import admin, comunicados, enquetes, financeiro, interfone, morador, residentes, site, votacao  # noqa: E402

app.include_router(site.router)
app.include_router(morador.router)
app.include_router(admin.router)
app.include_router(financeiro.router)
app.include_router(votacao.router)
app.include_router(interfone.router)
app.include_router(comunicados.router)
app.include_router(residentes.router)
app.include_router(enquetes.router)


if __name__ == "__main__":
    lista = list(unidades_padrao())
    assert len(lista) == 12 * 8 + 15 * 20 + 2 == 398
    assert ("01", "004") in lista and ("01", "104") in lista and ("01", "201") not in lista
    assert ("27", "404") in lista and ("13", "001") in lista
    print("seed ok")
