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
# Conta que envia os avisos internos do site (para logs@ e contato@). Se vazia, usa a conta principal.
SMTP_NOREPLY_USER = os.environ.get("SMTP_NOREPLY_USER", "")
SMTP_NOREPLY_PASS = os.environ.get("SMTP_NOREPLY_PASS", "")
MAIL_CONTATO = os.environ.get("MAIL_CONTATO", "contato@jardimindependencia.com.br")
MAIL_LOGS = os.environ.get("MAIL_LOGS", "logs@jardimindependencia.com.br")
SITE_URL = os.environ.get("SITE_URL", "https://jardimindependencia.com.br")
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "")  # vazio = sem antivírus (só assinatura interna do arquivo)
CLAMAV_PORT = int(os.environ.get("CLAMAV_PORT", "3310"))

CONDOMINIO = {
    "nome": "Condomínio Residencial Jardim Independência",
    "endereco": "Av. Governador Hélio Gueiros, 48 — Quarenta Horas (Coqueiro)",
    "cidade": "Ananindeua — PA",
    "cep": "67120-370 e 67120-942",
    "referencia": "Em frente ao Colégio La Salle",
    "cnpj": "24.592.077/0001-66",
}
# APNs (push do app iOS). Vazio = envio desligado. A chave .p8 fica fora do repositório (data/apns/).
APNS_KEY_ID = os.environ.get("APNS_KEY_ID", "")
APNS_TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
APNS_TOPIC = os.environ.get("APNS_TOPIC", "br.com.jardimindependencia.app")
APNS_KEY_PATH = os.environ.get("APNS_KEY_PATH", "/data/apns/AuthKey.p8")
# FCM (push do app Android). Sem o JSON da conta de serviço = envio desligado. O JSON fica fora do repositório (data/fcm/).
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/data/fcm/service-account.json")
FCM_PROJECT_ID = os.environ.get("FCM_PROJECT_ID", "")  # vazio = usa o project_id do próprio JSON
