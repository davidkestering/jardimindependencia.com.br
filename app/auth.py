"""Sessão por cookie assinado, senha bcrypt, validação de CPF e rate limit de login."""
import re
import time
from collections import defaultdict
from datetime import date

import bcrypt
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from config import SECRET_KEY

_serializer = URLSafeSerializer(SECRET_KEY, salt="sessao")
COOKIE = "sessao"
SESSAO_HORAS = 12

# ponytail: rate limit em memória (1 processo). Trocar por Redis se houver mais de um worker.
_tentativas: dict[str, list[float]] = defaultdict(list)
MAX_TENTATIVAS, JANELA_S = 8, 15 * 60


def registrar_tentativa(chave: str) -> None:
    agora = time.time()
    _tentativas[chave] = [t for t in _tentativas[chave] if agora - t < JANELA_S] + [agora]


def bloqueado(chave: str) -> bool:
    agora = time.time()
    return len([t for t in _tentativas[chave] if agora - t < JANELA_S]) >= MAX_TENTATIVAS


def limpar_tentativas(chave: str) -> None:
    _tentativas.pop(chave, None)


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return bcrypt.checkpw(senha.encode(), senha_hash.encode())


def so_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def cpf_valido(cpf: str) -> bool:
    cpf = so_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(cpf[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10 % 11) % 10
        if dv != int(cpf[n]):
            return False
    return True


def criar_sessao(tipo: str, id_: str) -> str:
    return _serializer.dumps({"t": tipo, "id": id_, "exp": time.time() + SESSAO_HORAS * 3600})


def ler_sessao(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        dados = _serializer.loads(token)
    except BadSignature:
        return None
    return dados if dados.get("exp", 0) > time.time() else None


def exigir(tipo: str):
    def dep(request: Request):
        s = ler_sessao(request)
        if not s or s["t"] != tipo:
            destino = "/admin/login" if tipo == "admin" else "/morador/login"
            raise HTTPException(status_code=303, headers={"Location": f"{destino}?next={request.url.path}"})
        return s
    return dep


def parse_data(s: str) -> date | None:
    """Aceita dd/mm/aaaa ou aaaa-mm-dd."""
    s = (s or "").strip()
    try:
        if "/" in s:
            d, m, a = s.split("/")
            return date(int(a), int(m), int(d))
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":  # auto-verificação mínima
    assert cpf_valido("529.982.247-25") and not cpf_valido("111.111.111-11") and not cpf_valido("52998224724")
    assert verificar_senha("x", hash_senha("x")) and not verificar_senha("y", hash_senha("x"))
    assert parse_data("05/03/1990") == date(1990, 3, 5) and parse_data("1990-03-05") == date(1990, 3, 5) and parse_data("x") is None
    for _ in range(MAX_TENTATIVAS):
        registrar_tentativa("k")
    assert bloqueado("k") and not bloqueado("outro")
    print("auth ok")
