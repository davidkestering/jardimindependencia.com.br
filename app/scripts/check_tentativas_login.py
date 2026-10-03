"""Tentativas de login que falharam (detecção de invasão): toda falha, nas duas áreas, vai ao histórico como "Tentativa de login
... falhou (motivo)", inclusive a feita durante o bloqueio por excesso de tentativas; a tela Histórico tem o filtro exclusivo com
total e contagem por dia. Usa só o IP fictício 203.0.113.77 e o Bloco 99; limpa o que cria:
docker exec condominio-app python scripts/check_tentativas_login.py"""
import re, sys, time
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: enviados.append(assunto) or True

from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from main import app
from models import AdminUser, Historico, Morador
from termo import TERMO
from unidades_teste import unidades

IP, CPF, OUTRO = "203.0.113.77", "52998224725", "11144477735"
M, A = "Tentativa de login CONDÔMINO falhou", "Tentativa de login ADMIN falhou"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Historico).where(Historico.ip == IP)); db.execute(delete(Morador).where(Morador.cpf == CPF)); db.commit()
    auth._tentativas.clear()


def cap(certo=True):
    cp = auth.captcha_novo(); r = auth._captcha.loads(cp["token"])["r"]
    return {"captcha_token": cp["token"], "captcha": str(r if certo else r + 1)}


def ultima():
    with SessionLocal() as db:
        h = db.scalar(select(Historico).where(Historico.ip == IP).order_by(Historico.quando.desc()).limit(1))
        return h.acao, h.detalhe


limpar()
try:
    with SessionLocal() as db:
        u, = unidades(db)
        db.add(Morador(unidade_id=u.id, nome="Ana Pendente", cpf=CPF, nascimento=auth.parse_data("1980-05-10"), email="a@example.com", telefone="91999990000", status="pendente", termo_texto=TERMO)); db.commit()
        mestre = db.scalar(select(AdminUser.id).where(AdminUser.master))
    c = TestClient(app, base_url="https://t", headers={"x-real-ip": IP})
    cond = lambda cpf, nasc="10051980", certo=True: c.post("/morador/login", data={"cpf": cpf, "nascimento": nasc, **cap(certo)}).text
    adm = lambda senha="senha-errada-123", certo=True: c.post("/admin/login", data={"login": "invasor", "senha": senha, **cap(certo)}).text

    # 1. condômino: captcha errado, dados que não conferem e cadastro ainda não aprovado
    cond(CPF, certo=False); assert ultima()[0] == f"{M} (captcha)" and ultima()[1]["cpf"] == CPF
    cond(CPF, "01011990"); assert ultima()[0] == f"{M} (CPF ou data não conferem)"
    assert "aguard" in cond(CPF).lower() and ultima()[0] == f"{M} (pendente)" and ultima()[1]["nome"] == "Ana Pendente"
    r = c.post("/morador/escolher", data={"token": "forjado", "mid": "00000000-0000-0000-0000-000000000000"}); assert r.status_code == 403
    assert ultima()[0] == f"{M} (escolha de apartamento inválida ou expirada)"

    # 2. administração: captcha errado e login ou senha incorretos
    adm(certo=False); assert ultima()[0] == f"{A} (captcha)" and ultima()[1]["login"] == "invasor"
    assert "incorretos" in adm() and ultima()[0] == f"{A} (login ou senha incorretos)"

    # 3. durante o bloqueio por excesso de tentativas a tentativa também é registrada (só no histórico: sem e-mail a cada uma)
    for _ in range(auth.MAX_TENTATIVAS - 2):
        adm()
    for _ in range(auth.MAX_TENTATIVAS):
        cond(OUTRO, "01011990")
    time.sleep(0.4); n_mail = len(enviados)
    assert "Muitas tentativas" in adm() and ultima()[0] == f"{A} (bloqueado por excesso de tentativas)" and "senha" not in str(ultima()[1]).lower()
    assert "Muitas tentativas" in cond(OUTRO, "01011990") and ultima()[0] == f"{M} (bloqueado por excesso de tentativas)" and ultima()[1]["cpf"] == OUTRO
    time.sleep(0.4); assert len(enviados) == n_mail and n_mail == 4 + 2 * auth.MAX_TENTATIVAS  # as demais falhas avisam logs@

    # 4. filtro exclusivo no Histórico: total e contagem por dia do período; falhas antigas (nome anterior) contam, login realizado não
    with SessionLocal() as db:
        db.add(Historico(tipo=None, login=None, ip=IP, acao="Login CONDÔMINO recusado", detalhe={"cpf": CPF}))
        db.add(Historico(tipo="admin", login="x", ip=IP, acao="Login ADMIN realizado", detalhe={})); db.commit()
    n_cond, n_adm = 4 + auth.MAX_TENTATIVAS + 1 + 1, auth.MAX_TENTATIVAS + 1  # condômino: 4 + 8 + bloqueada + a antiga; admin: 8 + bloqueada
    ac = TestClient(app, base_url="https://t"); ac.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(mestre)))
    hoje = datetime.now(mail.FUSO).date(); ontem = hoje - timedelta(days=1)
    assert 'href="/admin/historico?tentativas=1"' in ac.get("/admin/historico").text
    pg = ac.get(f"/admin/historico?tentativas=1&q={IP}").text  # sem período: abre no dia de hoje
    assert "Tentativas de login que falharam" in pg and f'name="de" type="date" value="{hoje}"' in pg
    assert f"<strong>{n_cond + n_adm}</strong> tentativa(s)" in pg, re.findall(r"<strong>\d+</strong> tentativa", pg)
    assert re.search(rf"<td>{hoje:%d/%m/%Y}</td>\s*<td>{n_cond + n_adm}</td>\s*<td>{n_cond}</td>\s*<td>{n_adm}</td>", pg), pg[pg.find("Por dia"):][:400]
    assert "bloqueado por excesso de tentativas" in pg and "Login CONDÔMINO recusado" in pg and "Login ADMIN realizado" not in pg
    assert "<strong>0</strong> tentativa(s)" in ac.get(f"/admin/historico?tentativas=1&de={ontem}&ate={ontem}&q={IP}").text
    assert "Login ADMIN realizado" in ac.get(f"/admin/historico?de={hoje}&ate={hoje}&q={IP}").text  # o histórico geral continua completo
    print("check_tentativas_login ok")
finally:
    limpar()
