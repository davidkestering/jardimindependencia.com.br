import logging
import smtplib
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from email.message import EmailMessage

from config import MAIL_CONTATO, MAIL_LOGS, SMTP_HOST, SMTP_NOREPLY_PASS, SMTP_NOREPLY_USER, SMTP_PASS, SMTP_PORT, SMTP_USER

log = logging.getLogger("mail")
FUSO = ZoneInfo("America/Belem")


def ip_de(request) -> str:
    """IP real do visitante: X-Real-IP vem do Nginx e não é forjável pelo cliente."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")


def _gravar_historico(tipo, login, ip, acao, dados) -> None:
    from db import SessionLocal
    from models import Historico
    try:
        with SessionLocal() as db:
            db.add(Historico(tipo=tipo, login=login, ip=ip, acao=acao[:200], detalhe={k: str(v)[:2000] for k, v in dados.items()}))
            db.commit()
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação; fica registrada no log do container
        log.exception("falha ao gravar histórico: %s", acao)


def registrar(assunto: str, request, **dados) -> None:
    """Auditoria: grava em `historico` (quem, IP, data/hora, detalhes) e envia e-mail para MAIL_LOGS. Em thread."""
    sessao = getattr(request.state, "sessao", None) or {}
    tipo, login, ip = sessao.get("t"), sessao.get("login"), ip_de(request)
    if not login:  # antes de existir sessão (logins, cadastro no site): usa o identificador informado na própria ação
        cpf = "".join(ch for ch in str(dados.get("cpf") or "") if ch.isdigit())
        cpf = f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else str(dados.get("cpf") or "")
        login = str(dados.get("login") or "") or (f"{dados['nome']} ({cpf})" if dados.get("nome") and cpf else None)
        tipo = tipo or ("admin" if dados.get("login") else ("morador" if login else None))
    linhas = [f"Data/hora: {datetime.now(FUSO):%d/%m/%Y %H:%M:%S}", f"IP: {ip}", f"Quem: {login or 'visitante'}",
              f"Navegador: {request.headers.get('user-agent', '')[:200]}", ""]
    linhas += [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in dados.items()]
    _gravar_historico(tipo, login, ip, assunto, dados)
    threading.Thread(target=enviar, args=(MAIL_LOGS, f"[Log] {assunto}", "\n".join(linhas)), daemon=True).start()


def notificar(para: str, assunto: str, corpo: str) -> None:
    """Envio em thread para não atrasar a resposta HTTP."""
    threading.Thread(target=enviar, args=(para, assunto, corpo), daemon=True).start()


def conta_para(para: str) -> tuple[str, str]:
    """Remetente por destino: avisos internos (logs@ e contato@) saem de no-reply@; condôminos recebem de contato@."""
    if SMTP_NOREPLY_USER and para.lower() in (MAIL_CONTATO.lower(), MAIL_LOGS.lower()):
        return SMTP_NOREPLY_USER, SMTP_NOREPLY_PASS
    return SMTP_USER, SMTP_PASS


def enviar(para: str, assunto: str, corpo: str, responder_para: str | None = None) -> bool:
    usuario, senha = conta_para(para)
    msg = EmailMessage()
    msg["From"] = f"Condomínio Jardim Independência <{usuario or MAIL_CONTATO}>"
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
            if usuario:
                s.login(usuario, senha)
            s.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 — registra e devolve False para a UI avisar
        log.exception("falha ao enviar e-mail para %s", para)
        return False
