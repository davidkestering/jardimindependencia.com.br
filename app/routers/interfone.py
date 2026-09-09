import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
import interfone as ifone
from db import SessionLocal, get_db
from models import AdminUser, Morador, PushSubscription, Unidade
from routers.admin import admin_dep
from routers.morador import morador_atual

router = APIRouter()


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


def unidade_da_sessao(sessao: dict | None, db: Session) -> uuid.UUID | None:
    if not sessao:
        return None
    if sessao["t"] == "morador":
        m = db.get(Morador, uuid.UUID(sessao["id"]))
        return m.unidade_id if m and m.status == "aprovado" else None
    if sessao["t"] == "admin" and db.get(AdminUser, uuid.UUID(sessao["id"])):
        return db.scalar(select(Unidade.id).where(Unidade.bloco == "ADMINISTRACAO"))
    return None


def destinos(db: Session):
    blocos = sorted({u.bloco for u in db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.ativa))})
    return blocos


@router.get("/morador/interfone")
def pagina_morador(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    return render(request, "interfone.html", blocos=destinos(db), unidade=m.unidade, menu="morador", vapid=ifone.vapid_keys()[1])


@router.get("/admin/interfone")
def pagina_admin(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = db.scalar(select(Unidade).where(Unidade.bloco == "ADMINISTRACAO"))
    return render(request, "interfone.html", blocos=destinos(db), unidade=u, menu="admin", vapid=None)


@router.post("/interfone/push")
async def salvar_push(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    dados = await request.json()
    endpoint, keys = dados.get("endpoint", ""), dados.get("keys", {})
    if not endpoint.startswith("https://") or not {"p256dh", "auth"} <= set(keys):
        raise HTTPException(400)
    s = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if s:
        s.morador_id, s.keys = m.id, keys
    else:
        db.add(PushSubscription(morador_id=m.id, endpoint=endpoint, keys=keys))
    db.commit()
    return JSONResponse({"ok": True})


@router.websocket("/ws/interfone")
async def ws_interfone(ws: WebSocket):
    sessao = auth.ler_sessao(ws)
    with SessionLocal() as db:
        minha = unidade_da_sessao(sessao, db)
    if not minha:
        await ws.close(code=4401)
        return
    await ws.accept()
    ifone.registrar(minha, ws)
    try:
        # chamadas ainda tocando para esta unidade (ex.: abriu pelo push)
        for cid, p in list(ifone.pendentes.items()):
            if p["para"] == minha:
                await ifone.enviar(ws, {"t": "tocando", "chamada": str(cid), "de": p["de_rotulo"], "de_id": str(p["de"])})
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            t = msg.get("t")
            if t == "chamar":
                with SessionLocal() as db:
                    if msg.get("bloco") in ("PORTARIA", "ADMINISTRACAO"):
                        alvo = db.scalar(select(Unidade).where(Unidade.bloco == msg["bloco"]))
                    else:
                        alvo = db.scalar(select(Unidade).where(Unidade.bloco == str(msg.get("bloco", "")).zfill(2),
                                                               Unidade.apto == auth.so_digitos(str(msg.get("apto", ""))).zfill(3), Unidade.ativa))
                if not alvo or alvo.id == minha:
                    await ifone.enviar(ws, {"t": "erro", "msg": "Unidade não encontrada."})
                    continue
                if any(p["de"] == minha for p in ifone.pendentes.values()):
                    await ifone.enviar(ws, {"t": "erro", "msg": "Já existe uma chamada em andamento."})
                    continue
                await ifone.enviar(ws, await ifone.iniciar_chamada(minha, alvo.id))
            elif t in ("atender", "recusar", "desligar", "sdp", "ice"):
                try:
                    cid = uuid.UUID(msg.get("chamada", ""))
                except ValueError:
                    continue
                if t == "atender":
                    if not await ifone.atender(cid, minha):
                        await ifone.enviar(ws, {"t": "encerrada", "chamada": str(cid), "status": "perdida"})
                elif t == "recusar":
                    await ifone.encerrar(cid, "recusada", origem=minha)
                elif t == "desligar":
                    await ifone.encerrar(cid, "encerrada", origem=minha)
                else:
                    await ifone.retransmitir(cid, minha, {"t": t, "chamada": str(cid), "dados": msg.get("dados")})
    except WebSocketDisconnect:
        pass
    finally:
        ifone.remover(minha, ws)
        for cid, p in list(ifone.pendentes.items()):
            if minha in (p["de"], p["para"]) and not ifone.conexoes.get(minha):
                asyncio.ensure_future(ifone.encerrar(cid, "perdida" if p["para"] == minha else "encerrada", origem=minha))
