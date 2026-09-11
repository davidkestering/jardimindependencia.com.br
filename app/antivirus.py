"""Varredura de uploads com ClamAV (clamd, protocolo INSTREAM por TCP). Sem dependência extra.
Falha fechada: se o antivírus estiver configurado e indisponível, o arquivo é recusado."""
import logging
import socket
import struct
from pathlib import Path

from config import CLAMAV_HOST, CLAMAV_PORT

log = logging.getLogger("antivirus")
BLOCO = 1024 * 1024


def escanear(caminho: Path) -> tuple[bool, str]:
    """(limpo, detalhe). Sem CLAMAV_HOST: (True, 'sem antivírus')."""
    if not CLAMAV_HOST:
        return True, "sem antivírus"
    try:
        with socket.create_connection((CLAMAV_HOST, CLAMAV_PORT), timeout=60) as s:
            s.sendall(b"zINSTREAM\0")
            with caminho.open("rb") as f:
                while bloco := f.read(BLOCO):
                    s.sendall(struct.pack("!I", len(bloco)) + bloco)
            s.sendall(struct.pack("!I", 0))
            resposta = b""
            while not resposta.endswith(b"\0"):
                parte = s.recv(4096)
                if not parte:
                    break
                resposta += parte
    except OSError as e:
        log.error("clamav indisponível (%s): upload recusado", e)
        return False, "antivírus indisponível"
    texto = resposta.decode(errors="replace").strip("\0").strip()
    if texto.endswith("OK"):
        return True, "limpo"
    log.warning("clamav: %s -> %s", caminho.name, texto)
    return False, texto.replace("stream: ", "")


if __name__ == "__main__":
    import tempfile
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(eicar); p = Path(f.name)
    print("eicar:", escanear(p))
    p.write_bytes(b"%PDF-1.4 limpo"); print("limpo:", escanear(p))
    p.unlink()
