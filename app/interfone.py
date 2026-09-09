"""Interfone virtual: registro de conexões WebSocket por unidade, chamadas pendentes e push.
ponytail: estado em memória (1 worker uvicorn). Trocar por Redis pub/sub se houver mais de um processo."""
import asyncio
import json
import os
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import WebSocket
from sqlalchemy import select

from config import MAIL_CONTATO, UPLOAD_DIR
from db import SessionLocal
from models import Chamada, Morador, PushSubscription, Unidade

log = logging.getLogger("interfone")
TOQUE_S = 45

conexoes: dict[uuid.UUID, set[WebSocket]] = {}
pendentes: dict[uuid.UUID, dict] = {}   # chamada_id -> {de, para, de_rotulo, timer}

_vapid_dir = Path(os.environ.get("VAPID_DIR", "/data/vapid"))


def vapid_keys() -> tuple[str, str]:
    """(pem_privada, chave_publica_base64url). Gera uma vez e guarda em data/vapid/."""
    _vapid_dir.mkdir(parents=True, exist_ok=True)
    pem, pub = _vapid_dir / "private.pem", _vapid_dir / "public.txt"
    if not pem.exists():
        from py_vapid import Vapid, b64urlencode
        v = Vapid()
        v.generate_keys()
        v.save_key(str(pem))
        raw = v.public_key.public_bytes(__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
                                        __import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint)
        pub.write_text(b64urlencode(raw))
    return pem.read_text(), pub.read_text().strip()


async def enviar(ws: WebSocket, msg: dict):
    try:
        await ws.send_text(json.dumps(msg))
    except Exception:  # noqa: BLE001
        pass


async def broadcast(unidade_id: uuid.UUID, msg: dict) -> int:
    alvos = list(conexoes.get(unidade_id, ()))
    for ws in alvos:
        await enviar(ws, msg)
    return len(alvos)


def registrar(unidade_id: uuid.UUID, ws: WebSocket):
    conexoes.setdefault(unidade_id, set()).add(ws)


def remover(unidade_id: uuid.UUID, ws: WebSocket):
    s = conexoes.get(unidade_id)
    if s:
        s.discard(ws)
        if not s:
            conexoes.pop(unidade_id, None)


def push_para_unidade(unidade_id: uuid.UUID, payload: dict):
    """Web push para todos os moradores da unidade (executado em thread para não travar o loop)."""
    from pywebpush import WebPushException, webpush
    pem, _ = vapid_keys()
    with SessionLocal() as db:
        subs = db.scalars(select(PushSubscription).join(Morador, Morador.id == PushSubscription.morador_id)
                          .where(Morador.unidade_id == unidade_id)).all()
        for s in subs:
            try:
                webpush({"endpoint": s.endpoint, "keys": s.keys}, json.dumps(payload), vapid_private_key=pem,
                        vapid_claims={"sub": f"mailto:{MAIL_CONTATO}"}, ttl=TOQUE_S)
            except WebPushException as e:
                log.warning("push falhou (%s): %s", s.endpoint[:40], e)
                if e.response is not None and e.response.status_code in (404, 410):
                    db.delete(s)
        db.commit()


def rotulo(db, unidade_id) -> str:
    u = db.get(Unidade, unidade_id)
    return u.rotulo if u else "?"


async def iniciar_chamada(de: uuid.UUID, para: uuid.UUID) -> dict:
    with SessionLocal() as db:
        c = Chamada(de_unidade_id=de, para_unidade_id=para)
        db.add(c)
        db.commit()
        cid, de_rot, para_rot = c.id, rotulo(db, de), rotulo(db, para)
    msg = {"t": "tocando", "chamada": str(cid), "de": de_rot, "de_id": str(de)}
    pendentes[cid] = {"de": de, "para": para, "de_rotulo": de_rot, "para_rotulo": para_rot}
    n = await broadcast(para, msg)
    if n == 0:
        try:
            await asyncio.to_thread(push_para_unidade, para, {"titulo": "Interfone: chamada de " + de_rot,
                                                                "corpo": "Toque para atender", "url": f"/morador/interfone?chamada={cid}"})
        except Exception:  # noqa: BLE001 — push é melhor esforço; a chamada continua tocando
            log.exception("push para %s falhou", para_rot)
    pendentes[cid]["timer"] = asyncio.get_event_loop().call_later(TOQUE_S, lambda: asyncio.ensure_future(encerrar(cid, "perdida")))
    return {"t": "chamando", "chamada": str(cid), "para": para_rot}


async def encerrar(cid: uuid.UUID, status: str, origem: uuid.UUID | None = None):
    p = pendentes.pop(cid, None)
    if not p:
        return
    if t := p.get("timer"):
        t.cancel()
    with SessionLocal() as db:
        c = db.get(Chamada, cid)
        if c and c.status in ("tocando", "atendida"):
            c.status = status
            c.encerrada_em = datetime.now(timezone.utc)
            db.commit()
    msg = {"t": "encerrada", "chamada": str(cid), "status": status}
    for uid in (p["de"], p["para"]):
        if uid != origem:
            await broadcast(uid, msg)


async def atender(cid: uuid.UUID, quem: uuid.UUID) -> bool:
    p = pendentes.get(cid)
    if not p or p["para"] != quem:
        return False
    if t := p.get("timer"):
        t.cancel()
    with SessionLocal() as db:
        c = db.get(Chamada, cid)
        if c:
            c.status = "atendida"
            db.commit()
    await broadcast(p["de"], {"t": "atendida", "chamada": str(cid)})
    return True


async def retransmitir(cid: uuid.UUID, de: uuid.UUID, msg: dict):
    p = pendentes.get(cid)
    if not p or de not in (p["de"], p["para"]):
        return
    destino = p["para"] if de == p["de"] else p["de"]
    await broadcast(destino, msg)
