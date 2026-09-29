"""Push nativo do app iOS via APNs (HTTP/2 + JWT ES256). Tokens em `dispositivo_app`; token morto (410 / BadDeviceToken /
Unregistered) é apagado na hora. Sem chave configurada, `notificar` não faz nada.
ponytail: envio em thread, sem fila persistente; retentativa simples (3x) em 429/500/503. Fila só se o volume crescer."""
import base64
import json
import logging
import threading
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from config import APNS_KEY_ID, APNS_KEY_PATH, APNS_TEAM_ID, APNS_TOPIC
from db import SessionLocal
from models import DispositivoApp, Morador

log = logging.getLogger("apns")
HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
TOKEN_MORTO = ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic")
JWT_VALIDADE_S = 40 * 60  # Apple: renovar entre 20 e 60 min
KEY_PATH = Path(APNS_KEY_PATH)
_jwt: tuple[float, str] = (0.0, "")
_lock = threading.Lock()


def ativo() -> bool:
    return bool(APNS_KEY_ID and APNS_TEAM_ID and KEY_PATH.exists())


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt() -> str:
    """JWT ES256 assinado com a chave .p8; reaproveitado por JWT_VALIDADE_S."""
    global _jwt
    with _lock:
        if time.time() - _jwt[0] < JWT_VALIDADE_S:
            return _jwt[1]
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        cab = _b64(json.dumps({"alg": "ES256", "kid": APNS_KEY_ID}).encode())
        corpo = _b64(json.dumps({"iss": APNS_TEAM_ID, "iat": int(time.time())}).encode())
        chave = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
        r, s = decode_dss_signature(chave.sign(f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256())))
        _jwt = (time.time(), f"{cab}.{corpo}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}")
        return _jwt[1]


def payload(titulo: str, corpo: str, url: str) -> bytes:
    """`url` fora de `aps` é lida pelo app: ao tocar, abre esse caminho na aba correspondente."""
    return json.dumps({"aps": {"alert": {"title": titulo, "body": corpo}, "sound": "default", "badge": 1}, "url": url}).encode()


def enviar(cli: httpx.Client, d: DispositivoApp, dados: bytes) -> bool:
    """Envia a um aparelho. Retorna True se o token está morto e deve ser apagado."""
    cab = {"authorization": f"bearer {jwt()}", "apns-topic": APNS_TOPIC, "apns-push-type": "alert", "apns-priority": "10"}
    for tentativa in range(3):
        try:
            r = cli.post(f"{HOSTS.get(d.ambiente, HOSTS['production'])}/3/device/{d.token}", content=dados, headers=cab)
        except httpx.HTTPError as e:
            log.warning("apns rede (%s…): %s", d.token[:8], e)
            time.sleep(2 ** tentativa)
            continue
        if r.status_code == 200:
            return False
        motivo = (r.json() or {}).get("reason", "") if r.content else ""
        log.warning("apns %s %s (%s…)", r.status_code, motivo, d.token[:8])
        if r.status_code == 410 or motivo in TOKEN_MORTO:
            return True
        if r.status_code in (429, 500, 503):
            time.sleep(2 ** tentativa)
            continue
        return False  # 400/403 etc.: não retenta
    return False


def _rodar(titulo: str, corpo: str, url: str, filtro: tuple) -> None:
    with SessionLocal() as db:
        disp = db.scalars(select(DispositivoApp).join(Morador, Morador.id == DispositivoApp.morador_id)
                          .where(Morador.status == "aprovado", *filtro)).all()
        if not disp:
            return
        dados = payload(titulo, corpo, url)
        with httpx.Client(http2=True, timeout=10) as cli:
            for d in disp:
                if enviar(cli, d, dados):
                    db.delete(d)
        db.commit()


def notificar(titulo: str, corpo: str, url: str, *, morador_id: uuid.UUID | None = None, unidade_id: uuid.UUID | None = None) -> None:
    """Push para todos os condôminos aprovados, ou só para um morador / uma unidade. Em thread; melhor esforço."""
    if not ativo():
        return
    filtro = (Morador.id == morador_id,) if morador_id else (Morador.unidade_id == unidade_id,) if unidade_id else ()
    threading.Thread(target=_rodar, args=(titulo, corpo[:200], url, filtro), daemon=True).start()


if __name__ == "__main__":  # auto-verificação: JWT válido com chave gerada na hora + tratamento de resposta da APNs
    import tempfile
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    k = ec.generate_private_key(ec.SECP256R1())
    with tempfile.NamedTemporaryFile(suffix=".p8", delete=False) as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID = Path(f.name), "KEY1234567", "TEAM123456"
    t = jwt(); cab, corpo, ass = t.split(".")
    assert json.loads(base64.urlsafe_b64decode(cab + "==")) == {"alg": "ES256", "kid": "KEY1234567"}
    raw = base64.urlsafe_b64decode(ass + "==")
    k.public_key().verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{cab}.{corpo}".encode(), ec.ECDSA(hashes.SHA256()))
    assert jwt() == t  # reaproveita
    p = json.loads(payload("T", "C", "/morador/x")); assert p["aps"]["alert"]["title"] == "T" and p["url"] == "/morador/x"

    class Cli:
        def __init__(self, *resps): self.resps = list(resps); self.chamadas = 0
        def post(self, *a, **k):
            self.chamadas += 1; st, motivo = self.resps.pop(0)
            return httpx.Response(st, json={"reason": motivo} if motivo else None)
    time.sleep = lambda s: None
    d = DispositivoApp(token="ab" * 32, ambiente="sandbox")
    assert enviar(Cli((200, "")), d, b"{}") is False
    assert enviar(Cli((410, "Unregistered")), d, b"{}") is True
    assert enviar(Cli((400, "BadDeviceToken")), d, b"{}") is True
    assert enviar(Cli((400, "BadMessageId")), d, b"{}") is False  # 400 comum: não retenta
    c = Cli((503, ""), (429, "TooManyRequests"), (200, "")); assert enviar(c, d, b"{}") is False and c.chamadas == 3
    print("apns ok")
