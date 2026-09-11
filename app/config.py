import os

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET_KEY = os.environ["SECRET_KEY"]
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "admin")
ADMIN_SENHA_INICIAL = os.environ.get("ADMIN_SENHA_INICIAL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "mailu-front")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_CONTATO = os.environ.get("MAIL_CONTATO", "contato@jardimindependencia.com.br")
MAIL_LOGS = os.environ.get("MAIL_LOGS", "logs@jardimindependencia.com.br")
SITE_URL = os.environ.get("SITE_URL", "https://jardimindependencia.com.br")

CONDOMINIO = {
    "nome": "Condomínio Residencial Jardim Independência",
    "endereco": "Av. Governador Hélio Gueiros, 48 — Quarenta Horas (Coqueiro)",
    "cidade": "Ananindeua — PA",
    "cep": "67120-370 e 67120-942",
    "referencia": "Em frente ao Colégio La Salle",
    "cnpj": "24.592.077/0001-66",
}
