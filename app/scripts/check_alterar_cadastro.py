"""Alteração dos dados do cadastro pela administração, com justificativa obrigatória. Só Bloco 99; limpa o que cria:
docker exec condominio-app python scripts/check_alterar_cadastro.py"""
import sys
sys.path.insert(0, "/app")
import mail
enviados, historico = [], []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True
mail._gravar_historico = lambda tipo, login, ip, acao, dados: historico.append((acao, dados))
mail.notificar = lambda para, assunto, corpo: mail.enviar(para, assunto, corpo)  # síncrono no teste

from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
import routers.admin as radmin
from db import SessionLocal
from main import app
from models import AdminUser, Morador, Unidade, permissoes_totais
from termo import TERMO
import unidades_teste  # noqa: F401  liga a visão das unidades de teste (Bloco 99) neste processo

radmin.notificar = mail.notificar
A, B, LOGIN = "52998224725", "11144477735", "check_alterar_sem_perm"


def limpar():
    with SessionLocal() as db:
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.execute(delete(AdminUser).where(AdminUser.login == LOGIN)); db.commit()


def cliente(id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao("admin", str(id_))); return c


limpar()
try:
    with SessionLocal() as db:
        adm = db.scalar(select(AdminUser).where(AdminUser.master, AdminUser.excluido_em.is_(None)))
        u = db.scalar(select(Unidade).where(Unidade.bloco == "99", Unidade.apto == "999"))
        m = Morador(unidade_id=u.id, nome="Ana Errada", cpf=A, nascimento=date(1980, 5, 10), email="ana@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO)
        sem = AdminUser(login=LOGIN, senha_hash="x", nome="Sem Alterar", areas={"moradores": ["ver", "criar", "aprovar", "encerrar"]})
        db.add_all([m, sem]); db.commit(); mid, sid = m.id, sem.id
    assert "alterar" in permissoes_totais(["moradores"])["moradores"]
    ac, url = cliente(adm.id), f"/admin/moradores/{mid}/alterar"
    base = dict(nome="Ana Errada", cpf=A, nascimento="1980-05-10", email="ana@example.com", telefone="91999990000", justificativa="Moradora digitou a data errada no cadastro")
    post = lambda c=ac, **k: c.post(url, data={**base, **k}, follow_redirects=False)
    assert "Alterar dados do cadastro" in ac.get(f"/admin/moradores/{mid}").text
    # recusas: sem permissão, sem justificativa, justificativa curta, dados inválidos, nada mudou
    sc = cliente(sid)
    assert post(sc, nascimento="1985-05-10").status_code == 403 and "Alterar dados do cadastro" not in sc.get(f"/admin/moradores/{mid}").text
    assert post(nascimento="1985-05-10", justificativa="").status_code in (400, 422)
    assert post(nascimento="1985-05-10", justificativa="erro").status_code == 400
    assert post(nascimento="2999-01-01").status_code == 400 and post(cpf="11111111111").status_code == 400 and post(email="x").status_code == 400
    assert post().status_code == 400
    with SessionLocal() as db:
        assert db.get(Morador, mid).nascimento == date(1980, 5, 10) and not historico and not enviados
    # alteração válida: grava, audita antes/depois + justificativa, avisa o condômino sem expor valores
    assert post(nascimento="1985-05-10", cpf=B).status_code == 303
    with SessionLocal() as db:
        x = db.get(Morador, mid); assert (x.nascimento, x.cpf, x.nome) == (date(1985, 5, 10), B, "Ana Errada")
    acao, det = historico[-1]
    assert acao == radmin.ACAO_ALTERADO and det["morador_id"] == mid and det["justificativa"] == base["justificativa"]
    assert "Nascimento: 10/05/1980 → 10/05/1985" in det["alteracoes"] and "CPF: " in det["alteracoes"] and "Nome" not in det["alteracoes"]
    assert len(enviados) == 1 and enviados[0][0] == "ana@example.com" and "CPF, Nascimento" in enviados[0][2] and "1985" not in enviados[0][2]
    # registro encerrado é histórico: não altera
    assert ac.post(f"/admin/moradores/{mid}/status", data={"status": "negado"}, follow_redirects=False).status_code == 303
    assert post(nome="Outra Pessoa").status_code == 400 and "Alterar dados do cadastro" not in ac.get(f"/admin/moradores/{mid}").text
    print("check_alterar_cadastro ok")
finally:
    limpar()
