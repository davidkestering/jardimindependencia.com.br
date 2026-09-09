import logging
import smtplib
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage

from config import MAIL_CONTATO, MAIL_LOGS, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER

log = logging.getLogger("mail")
FUSO = ZoneInfo("America/Belem")


def ip_de(request) -> str:
    """IP real do visitante: X-Real-IP vem do Nginx e não é forjável pelo cliente."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def registrar(assunto: str, request, **dados) -> None:
    """Auditoria por e-mail para MAIL_LOGS. Roda em thread para não atrasar a resposta."""
    linhas = [f"Data/hora: {datetime.now(FUSO):%d/%m/%Y %H:%M:%S}", f"IP: {ip_de(request)}",
              f"Navegador: {request.headers.get('user-agent', '')[:200]}", ""]
    linhas += [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in dados.items()]
    threading.Thread(target=enviar, args=(MAIL_LOGS, f"[Log] {assunto}", "\n".join(linhas)), daemon=True).start()


def enviar(para: str, assunto: str, corpo: str, responder_para: str | None = None) -> bool:
    msg = EmailMessage()
    msg["From"] = SMTP_USER or MAIL_CONTATO
    msg["To"] = para
    msg["Subject"] = assunto
    if responder_para:
        msg["Reply-To"] = responder_para
    msg.set_content(corpo)
    try:
        cliente = smtplib.SMTP_SSL if SMTP_PORT == 465 else smtplib.SMTP
        with cliente(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            if SMTP_PORT != 465:
                s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 — registra e devolve False para a UI avisar
        log.exception("falha ao enviar e-mail para %s", para)
        return False
