"""Push nativo do app Android via FCM (API HTTP v1 + OAuth2 da conta de serviço). Quem percorre os aparelhos e escolhe o canal
é `apns._rodar`; aqui ficam a credencial, a mensagem e o tratamento da resposta. Token morto (UNREGISTERED / SENDER_ID_MISMATCH)
é apagado por quem chama. Sem o JSON da conta de serviço, fica desligado.
ponytail: JWT RS256 feito com `cryptography` (já instalada), como no apns.py; firebase-admin só se precisar de mais que `messages:send`.
Conferir a credencial sem entregar nada: docker exec condominio-app python fcm.py validar"""
import base64
import json
import logging
import threading
import time
from pathlib import Path

import httpx

from config import FCM_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS

log = logging.getLogger("fcm")
ESCOPO = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_MORTO = ("UNREGISTERED", "SENDER_ID_MISMATCH")
ESPERA_MAX_S = 30  # teto para o Retry-After: o envio roda em thread e não pode ficar preso
FOLGA_S = 300  # renova o token OAuth2 5 min antes de vencer
CRED_PATH = Path(GOOGLE_APPLICATION_CREDENTIALS)
_acesso: tuple[float, str] = (0.0, "")  # (vence em, token OAuth2)
_lock = threading.Lock()


def ativo() -> bool:
    return CRED_PATH.is_file()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _conta() -> dict:
    return json.loads(CRED_PATH.read_text())


def url_envio() -> str:
    return f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID or _conta()['project_id']}/messages:send"


def token_acesso(cli: httpx.Client) -> str:
    """Token OAuth2 da conta de serviço (JWT RS256 trocado no token_uri); reaproveitado até perto de vencer."""
    global _acesso
    with _lock:
        if time.time() < _acesso[0]:
            return _acesso[1]
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        conta, agora = _conta(), int(time.time())
        uri = conta.get("token_uri", "https://oauth2.googleapis.com/token")
        cab = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        corpo = _b64(json.dumps({"iss": conta["client_email"], "scope": ESCOPO, "aud": uri, "iat": agora, "exp": agora + 3600}).encode())
        chave = serialization.load_pem_private_key(conta["private_key"].encode(), password=None)
        ass = _b64(chave.sign(f"{cab}.{corpo}".encode(), padding.PKCS1v15(), hashes.SHA256()))
        r = cli.post(uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{cab}.{corpo}.{ass}"})
        r.raise_for_status()
        d = r.json()
        _acesso = (time.time() + d.get("expires_in", 3600) - FOLGA_S, d["access_token"])
        return _acesso[1]


def mensagem(token: str, titulo: str, corpo: str, url: str, ttl: str | None = None, validar: bool = False) -> dict:
    """`notification` faz o Android exibir o aviso com o app fechado; `data.url` é lida pelo app ao tocar (valores de `data`
    são sempre strings); `avisos` é o canal que o app cria. `ttl` (ex.: "60s") para o que perde o sentido se atrasar."""
    android = {"priority": "HIGH", "notification": {"channel_id": "avisos"}, **({"ttl": ttl} if ttl else {})}
    return {"message": {"token": token, "notification": {"title": titulo, "body": corpo}, "data": {"url": url}, "android": android},
            **({"validate_only": True} if validar else {})}


def _erro(r: httpx.Response) -> tuple[str, str]:
    """(código, mensagem) do erro do FCM. O código específico (UNREGISTERED…) vem em error.details[].errorCode; sem ele, error.status."""
    try:
        e = r.json().get("error", {})
    except ValueError:
        return "", r.text[:200]
    return next((x["errorCode"] for x in e.get("details", []) if "errorCode" in x), e.get("status", "")), e.get("message", "")


def _espera(r: httpx.Response, tentativa: int) -> int:
    ra = r.headers.get("retry-after", "")
    return min(int(ra) if ra.isdigit() else 2 ** tentativa, ESPERA_MAX_S)


def enviar(cli: httpx.Client, msg: dict) -> bool:
    """Envia a um aparelho. Retorna True se o token está morto e deve ser apagado."""
    global _acesso
    tok, url = msg["message"]["token"][:8], url_envio()
    for tentativa in range(3):
        cab = {"authorization": f"Bearer {token_acesso(cli)}"}  # credencial com problema: estoura para quem chama
        try:
            r = cli.post(url, json=msg, headers=cab)
        except httpx.HTTPError as e:
            log.warning("fcm rede (%s…): %s", tok, e)
            time.sleep(2 ** tentativa)
            continue
        if r.status_code == 200:
            log.info("fcm 200 (%s…)", tok)
            return False
        cod, texto = _erro(r)
        log.warning("fcm %s %s (%s…): %s", r.status_code, cod, tok, texto)
        if cod in TOKEN_MORTO:
            return True
        if r.status_code == 401 and tentativa == 0:  # token OAuth2 invalidado antes de vencer: pede outro, uma vez só
            with _lock:
                _acesso = (0.0, "")
            continue
        if r.status_code in (429, 500, 503):
            time.sleep(_espera(r, tentativa))
            continue
        return False  # 400 INVALID_ARGUMENT (formato da mensagem), 401/403 de credencial: não retenta nem apaga o token
    return False


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["validar"]:  # credencial real, sem entregar nada: 400 INVALID_ARGUMENT sobre o token = autenticação OK
        if not ativo():
            sys.exit(f"sem credencial em {CRED_PATH}")
        with httpx.Client(http2=True, timeout=10) as cli:
            r = cli.post(url_envio(), headers={"authorization": f"Bearer {token_acesso(cli)}"},
                         json=mensagem(sys.argv[2] if len(sys.argv) > 2 else "teste-android", "Teste", "Validação da credencial", "/morador/comunicados", validar=True))
        print(r.status_code, r.text)
        sys.exit(0)

    # auto-verificação: formato da mensagem, JWT RS256 válido com chave gerada na hora e tratamento de resposta do FCM
    import tempfile
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    m = mensagem("tok", "T", "C", "/morador/x")
    assert m == {"message": {"token": "tok", "notification": {"title": "T", "body": "C"}, "data": {"url": "/morador/x"},
                             "android": {"priority": "HIGH", "notification": {"channel_id": "avisos"}}}}
    assert mensagem("tok", "T", "C", "/x", ttl="60s")["message"]["android"]["ttl"] == "60s"
    assert mensagem("tok", "T", "C", "/x", validar=True)["validate_only"] is True

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"client_email": "push@proj.iam.gserviceaccount.com", "private_key": pem, "project_id": "proj", "token_uri": "https://oauth.test/token"}, f)
    CRED_PATH, FCM_PROJECT_ID = Path(f.name), ""  # ignora a credencial e o projeto reais do ambiente
    assert ativo() and url_envio() == "https://fcm.googleapis.com/v1/projects/proj/messages:send"

    def erro(codigo, status):
        return {"error": {"status": status, "message": "m", "details": [{"@type": "x"}] + ([{"errorCode": codigo}] if codigo else [])}}

    class Cli:
        def __init__(self, *resps): self.resps, self.envios, self.oauth = list(resps), 0, []
        def post(self, url, **k):
            if url == "https://oauth.test/token":
                self.oauth.append(k["data"]["assertion"])
                return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600}, request=httpx.Request("POST", url))
            assert k["headers"]["authorization"] == "Bearer ya29.x"
            self.envios += 1; st, corpo, cab = self.resps.pop(0)
            return httpx.Response(st, json=corpo, headers=cab)
    esperas = []
    time.sleep = esperas.append
    c = Cli((200, {"name": "ok"}, {})); assert enviar(c, m) is False
    cab, corpo, ass = c.oauth[0].split(".")
    k.public_key().verify(base64.urlsafe_b64decode(ass + "=="), f"{cab}.{corpo}".encode(), padding.PKCS1v15(), hashes.SHA256())
    decl = json.loads(base64.urlsafe_b64decode(corpo + "=="))
    assert decl["iss"] == "push@proj.iam.gserviceaccount.com" and decl["scope"] == ESCOPO and decl["aud"] == "https://oauth.test/token"
    c = Cli((404, erro("UNREGISTERED", "NOT_FOUND"), {})); assert enviar(c, m) is True and c.oauth == []  # token OAuth2 reaproveitado
    assert enviar(Cli((403, erro("SENDER_ID_MISMATCH", "PERMISSION_DENIED"), {})), m) is True
    assert enviar(Cli((404, erro("", "NOT_FOUND"), {})), m) is False  # projeto errado não apaga token
    assert enviar(Cli((403, erro("", "PERMISSION_DENIED"), {})), m) is False  # credencial sem permissão não apaga token
    c = Cli((400, erro("INVALID_ARGUMENT", "INVALID_ARGUMENT"), {})); assert enviar(c, m) is False and c.envios == 1  # não retenta
    c = Cli((401, erro("", "UNAUTHENTICATED"), {}), (200, {}, {})); assert enviar(c, m) is False and c.envios == 2 and len(c.oauth) == 1  # renova e reenvia
    c = Cli((401, erro("", "UNAUTHENTICATED"), {}), (401, erro("", "UNAUTHENTICATED"), {})); assert enviar(c, m) is False and c.envios == 2  # desiste, sem apagar
    esperas.clear()
    c = Cli((503, erro("UNAVAILABLE", "UNAVAILABLE"), {"retry-after": "7"}), (429, erro("QUOTA_EXCEEDED", "RESOURCE_EXHAUSTED"), {"retry-after": "9999"}), (200, {}, {}))
    assert enviar(c, m) is False and c.envios == 3 and esperas == [7, ESPERA_MAX_S]
    print("fcm ok")
