import logging
import smtplib
from email.message import EmailMessage

from config import MAIL_CONTATO, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER

log = logging.getLogger("mail")


def enviar(para: str, assunto: str, corpo: str, responder_para: str | None = None) -> bool:
    msg = EmailMessage()
    msg["From"] = SMTP_USER or MAIL_CONTATO
    msg["To"] = para
    msg["Subject"] = assunto
    if responder_para:
        msg["Reply-To"] = responder_para
    msg.set_content(corpo)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 — registra e devolve False para a UI avisar
        log.exception("falha ao enviar e-mail para %s", para)
        return False
