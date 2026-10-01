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
from models import AdminUser, DispositivoApp, Morador, PushSubscription, Unidade
from routers.admin import admin_dep
from routers.morador import morador_atual

router = APIRouter()


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, **ctx})


def unidade_do_admin(db: Session, a: AdminUser) -> Unidade:
    """No interfone, a conta da portaria é a unidade PORTARIA; qualquer outro usuário da administração é a ADMINISTRACAO."""
    return db.scalar(select(Unidade).where(Unidade.bloco == ("PORTARIA" if a.portaria else "ADMINISTRACAO")))


def unidade_da_sessao(sessao: dict | None, db: Session) -> uuid.UUID | None:
    if not sessao:
        return None
    if sessao["t"] == "morador":
        m = db.get(Morador, uuid.UUID(sessao["id"]))
        return m.unidade_id if m and m.status == "aprovado" else None
    if sessao["t"] == "admin":
        a = db.get(AdminUser, uuid.UUID(sessao["id"]))
        return unidade_do_admin(db, a).id if a and not a.excluido_em else None  # usuário desativado não conecta
    return None


def destinos(db: Session) -> dict[str, list[str]]:
    """Mapa bloco -> aptos ativos, para os listbox de discagem."""
    from routers.financeiro import mapa_unidades
    return mapa_unidades(db)


APARELHOS = {"ios": "App iPhone", "android": "App Android"}


def interfones_ativos(db: Session, propria: Unidade, completa: bool) -> list[tuple[Unidade, bool, str]]:
    """Linhas (unidade, online agora, por onde toca com o site fechado) da tabela de interfones ativos: Portaria e
    Administração sempre; os apartamentos com condômino aprovado só na lista completa (administração), para o condômino
    não ver a presença dos vizinhos. Online = conexão aberta agora; quem está abrindo a página conta como online."""
    aprovado = Morador.status == "aprovado"
    avisos: dict[uuid.UUID, set[str]] = {}
    for uid, plataforma in db.execute(select(Morador.unidade_id, DispositivoApp.plataforma).join(DispositivoApp, DispositivoApp.morador_id == Morador.id).where(aprovado)):
        avisos.setdefault(uid, set()).add(APARELHOS.get(plataforma, plataforma))
    for uid in db.scalars(select(Morador.unidade_id).join(PushSubscription, PushSubscription.morador_id == Morador.id).where(aprovado)):
        avisos.setdefault(uid, set()).add("Navegador")
    unidades = db.scalars(select(Unidade).where(Unidade.apto == "").order_by(Unidade.bloco.desc())).all()  # Portaria, Administração
    if completa:
        unidades += db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.id.in_(select(Morador.unidade_id).where(aprovado)))
                               .order_by(Unidade.bloco, Unidade.apto)).all()
    online = set(ifone.conexoes) | {propria.id}
    return [(u, u.id in online, " · ".join(sorted(avisos.get(u.id, ())))) for u in unidades]


@router.get("/morador/interfone")
def pagina_morador(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    return render(request, "interfone.html", blocos=destinos(db), unidade=m.unidade, menu="morador", vapid=ifone.vapid_keys()[1],
                  ativos=interfones_ativos(db, m.unidade, completa=False))


@router.get("/admin/interfone")
def pagina_admin(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = unidade_do_admin(db, admin)
    return render(request, "interfone.html", blocos=destinos(db), unidade=u, menu="admin", vapid=None, ativos=interfones_ativos(db, u, completa=True))


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
        if not sessao:  # saiu em outra aba ou o cookie venceu: aceita só para o interfone.js ler o código e recarregar a página
            await ws.accept()
        await ws.close(code=ifone.SEM_SESSAO)
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
