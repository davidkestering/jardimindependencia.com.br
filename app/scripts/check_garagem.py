"""Garagem e Veículos: garagem de cada apartamento (convenção), sequência guiada do condômino (garagem própria, outras
garagens, veículos), consulta da administração e correção do vínculo com aviso ao condômino.
Limpa o que cria e desfaz a troca de garagem: docker exec condominio-app python scripts/check_garagem.py"""
import re
import sys
import uuid
sys.path.insert(0, "/app")
import mail, apns
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria
apns.notificar = lambda *a, **k: None  # teste nunca manda push aos condôminos

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from db import SessionLocal
from garagens import CONVENCAO
from main import app
from models import BLOCO_TESTE, LOGIN_TESTE, AdminUser, GaragemUtilizada, Morador, Unidade, Veiculo
from termo import TERMO
from unidades_teste import unidades

A, B = "52998224725", "11144477735"
ADM, SEM = "teste.garagem", "teste.semarea"  # usuários de administração do teste: com e sem a área Garagem e Veículos


def limpar():
    with SessionLocal() as db:
        ids = list(db.scalars(select(Unidade.id).where(Unidade.bloco == BLOCO_TESTE)))  # tudo o que houver nas unidades de teste é de teste
        db.execute(delete(Veiculo).where(Veiculo.unidade_id.in_(ids))); db.execute(delete(GaragemUtilizada).where(GaragemUtilizada.unidade_id.in_(ids)))
        trocadas = db.scalars(select(Unidade).where(Unidade.garagem_alterada_por == ADM)).all()  # vínculos que o teste trocou voltam ao que eram
        for u in trocadas:
            u.garagem = None
        db.flush()
        for u in trocadas:  # o teste só usa unidades que estavam com a garagem da convenção
            u.garagem, u.garagem_anterior, u.garagem_alterada_em, u.garagem_alterada_por = u.garagem_convencao, None, None, None
        for u in db.scalars(select(Unidade).where(Unidade.id.in_(ids))):
            u.garagem_uso = u.garagem_uso_para_id = u.garagens_informadas_em = None
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.execute(delete(AdminUser).where(AdminUser.login.in_([ADM, SEM]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


def post(c, rota, **dados):
    """Envia para a área do condômino e devolve a página resultante (com a mensagem de erro ou de confirmação)."""
    return c.post("/morador/garagem" + rota, data=dados).text


limpar()
try:
    with SessionLocal() as db:
        # convenção gravada no banco: uma garagem por apartamento, sem repetição; Portaria e Administração sem garagem
        aptos = {(u.bloco, u.apto): u for u in db.scalars(select(Unidade).where(Unidade.apto != "", Unidade.bloco != BLOCO_TESTE))}
        assert len(aptos) == 396 and all(aptos[k].garagem_convencao == n for k, n in CONVENCAO.items())
        assert len({u.garagem for u in aptos.values()}) == 396 and all(u.garagem is None for u in db.scalars(select(Unidade).where(Unidade.apto == "")))
        assert [aptos[k].garagem_convencao for k in (("01", "001"), ("03", "001"), ("17", "004"), ("27", "404"))] == [1, 11, 148, 384]

        # as quatro unidades de teste, sem nenhum dado de garagem (a 3ª e a 4ª ficam sem condômino: são as donas das garagens g3 e g4)
        u1, u2, u3, u4 = unidades(db, 4)
        assert all(u.garagem == u.garagem_convencao and not u.garagem_uso and not u.garagem_alterada_em for u in (u1, u2, u3, u4))
        (r1, g1), (r2, g2), (r3, g3), (r4, g4) = [(u.rotulo, u.garagem) for u in (u1, u2, u3, u4)]
        for u, nome, cpf in ((u1, "Ana Garagem", A), (u2, "Bia Garagem", B)):
            db.add(Morador(unidade_id=u.id, nome=nome, cpf=cpf, nascimento=auth.parse_data("1980-05-10"), email="a@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        for login, areas in ((ADM, ["garagem"]), (SEM, ["interfone"])):
            db.add(AdminUser(login=login, nome="Teste garagem", senha_hash=auth.hash_senha(uuid.uuid4().hex), areas=areas, criado_por="teste"))
        db.commit()
        ida, idb = [db.scalar(select(Morador.id).where(Morador.cpf == c)) for c in (A, B)]
        adm, sem, apple = [cliente("admin", db.scalar(select(AdminUser.id).where(AdminUser.login == login))) for login in (ADM, SEM, LOGIN_TESTE)]
    ca, cb = cliente("morador", ida), cliente("morador", idb)
    V = dict(marca="Fiat", modelo="Uno", cor="Prata")

    # 1. garagem ao lado do apartamento; antes de informar a garagem própria só existe essa pergunta
    pg = ca.get("/morador/garagem").text
    assert f"{r1} · Garagem {g1}" in pg and 'name="uso"' in pg and 'name="placa"' not in pg and "utiliza mais alguma" not in pg
    assert f"{r1} · Garagem {g1}" in ca.get("/morador").text
    assert "Informe primeiro as garagens" in post(ca, "/veiculos", **V, placa="ABC1D23", garagem=g1)

    # 2. garagem própria: alugada/cedida exige o apartamento que a utiliza, que não pode ser o próprio
    assert "informe o bloco e o apto" in post(ca, "/situacao", uso="alugada", para="")
    assert "não pode ser o próprio apartamento" in post(ca, "/situacao", uso="alugada", para=str(u1.id))
    pg = post(ca, "/situacao", uso="propria"); assert "utiliza mais alguma" in pg and 'name="placa"' not in pg
    assert "Alterar a situação" in pg and 'value="desfazer"' in pg  # já nesta etapa dá para alterar ou desfazer a resposta
    pg = post(ca, "/situacao", uso="desfazer"); assert "utiliza mais alguma" not in pg and 'name="uso"' in pg and 'value="desfazer"' not in pg
    with SessionLocal() as db:
        assert db.get(Unidade, u1.id).garagem_uso is None  # voltou a "não informado"
    post(ca, "/situacao", uso="propria")

    # 3. outras garagens: a pergunta se repete até o "não"; só então vêm os veículos
    assert "garagem do seu apartamento" in post(ca, "/utilizadas", garagem=g1)
    pg = post(ca, "/utilizadas", garagem=g3); assert "utiliza mais alguma" in pg and f"{r3} · Garagem {g3}" in pg and 'name="placa"' not in pg
    assert "já está na sua lista" in post(ca, "/utilizadas", garagem=g3)
    assert 'name="placa"' in post(ca, "/concluir")

    # 4. veículos: garagem obrigatória; placa válida e sem repetição; dois na mesma garagem; garagem fora da lista entra sozinha
    assert "Placa inválida (recebido: AB123)" in post(ca, "/veiculos", **V, placa="ab-123", garagem=g1)
    assert "Indique a garagem" in post(ca, "/veiculos", **V, placa="ABC1D23", garagem="")
    assert "ABC1D23" in post(ca, "/veiculos", **V, placa="abc-1d23", garagem=g1)
    assert "já está cadastrada" in post(ca, "/veiculos", **V, placa="ABC1D23", garagem=g3)
    post(ca, "/veiculos", **V, placa="DEF2G34", garagem=g1); post(ca, "/veiculos", **V, placa="GHI3J45", garagem=g2)
    with SessionLocal() as db:
        vs = db.scalars(select(Veiculo).where(Veiculo.unidade_id == u1.id, Veiculo.excluido_em.is_(None))).all()
        assert sorted((v.placa, v.garagem) for v in vs) == [("ABC1D23", g1), ("DEF2G34", g1), ("GHI3J45", g2)]
        us = db.scalars(select(GaragemUtilizada).where(GaragemUtilizada.unidade_id == u1.id, GaragemUtilizada.excluido_em.is_(None))).all()
        assert {(x.garagem, x.origem) for x in us} == {(g3, "informada"), (g2, "veiculo")}
        vid = {v.placa: v.id for v in vs}; ut2 = next(x.id for x in us if x.garagem == g2)

    assert "<td>RWV0C81</td>" in post(ca, "/veiculos", **V, placa="rwvOc81", garagem=g1)  # letra O no lugar do zero é corrigida
    with SessionLocal() as db:
        post(ca, f"/veiculos/{db.scalar(select(Veiculo.id).where(Veiculo.unidade_id == u1.id, Veiculo.placa == 'RWV0C81'))}/excluir")
    for i in range(12):  # sem teto de veículos por apartamento
        post(ca, "/veiculos", **V, placa=f"XYZ{i % 10}A{i:02d}", garagem=g1)
    with SessionLocal() as db:
        extras = db.scalars(select(Veiculo).where(Veiculo.unidade_id == u1.id, Veiculo.placa.like("XYZ%"))).all()
        assert len(extras) == 12
    for v in extras:
        post(ca, f"/veiculos/{v.id}/excluir")

    # 5. coerência: não cede a própria nem tira da lista uma garagem que ainda tem veículo do apartamento
    assert "Há veículo" in post(ca, "/situacao", uso="alugada", para=str(u2.id))
    assert "Há veículo" in post(ca, "/situacao", uso="desfazer")
    assert "Há veículo" in post(ca, f"/utilizadas/{ut2}/excluir")

    # 6. outro apartamento não mexe nos registros; garagem própria alugada/cedida não recebe veículo do apartamento
    assert cb.post(f"/morador/garagem/veiculos/{vid['ABC1D23']}/excluir").status_code == 404
    assert cb.post(f"/morador/garagem/utilizadas/{ut2}/excluir").status_code == 404
    post(cb, "/situacao", uso="alugada", para=str(u1.id)); post(cb, "/concluir")
    assert "está marcada como alugada/cedida" in post(cb, "/veiculos", **V, placa="JKL4M56", garagem=g2)
    for estranho in ("²", "99999999999", "12abc"):  # número que não é número não derruba a página
        assert "Escolha a garagem na lista." in post(ca, "/utilizadas", garagem=estranho)
    assert "não encontrada" in post(ca, "/utilizadas", garagem="404")  # 404 a 407 não são de apartamento

    # 7. remoção lógica do veículo: some da tela e fica no banco
    assert "<td>DEF2G34</td>" not in post(ca, f"/veiculos/{vid['DEF2G34']}/excluir")
    with SessionLocal() as db:
        assert db.get(Veiculo, vid["DEF2G34"]).excluido_em

    # 8. administração: quantas garagens e veículos por apartamento, com os detalhes; por padrão só quem tem registro
    pg = adm.get("/admin/garagem").text
    linha = re.search(rf"<td>{re.escape(r1)}</td>.*?</tr>", pg, re.S).group(0)
    assert f"<strong>3</strong> · nº {', '.join(str(n) for n in sorted((g1, g2, g3)))}" in linha and "<td><strong>2</strong>" in linha and "Uso próprio" in linha
    assert f"Alugada/cedida para {r1}" in re.search(rf"<td>{re.escape(r2)}</td>.*?</tr>", pg, re.S).group(0)
    assert f"<td>{r3}</td>" not in pg and f"<td>{r3}</td>" in adm.get("/admin/garagem?mostrar=todos").text
    pg = adm.get("/admin/garagem?q=ghi3j45").text; assert "GHI3J45" in pg and "ABC1D23" not in pg and "Fiat" in pg
    assert sem.get("/admin/garagem").status_code == 403  # sem a área liberada
    assert apple.post("/admin/garagem/corrigir", data={"unidade": str(u1.id), "garagem": g3}).status_code == 403  # conta de teste só consulta

    # 9. correção do vínculo: g3 era de outro apartamento, os dois trocam; o veículo da garagem própria acompanha; o condômino é avisado
    assert "inválid" in adm.post("/admin/garagem/corrigir", data={"unidade": str(u1.id), "garagem": 999}).text
    adm.post("/admin/garagem/corrigir", data={"unidade": str(u1.id), "garagem": g3})
    with SessionLocal() as db:
        n1, n3 = db.get(Unidade, u1.id), db.get(Unidade, u3.id)
        assert (n1.garagem, n1.garagem_anterior, n1.garagem_alterada_por) == (g3, g1, ADM) and (n3.garagem, n3.garagem_anterior) == (g1, g3)
        assert n1.garagem_convencao == g1 and db.get(Veiculo, vid["ABC1D23"]).garagem == g3 and db.get(Veiculo, vid["GHI3J45"]).garagem == g2
        assert [x.garagem for x in db.scalars(select(GaragemUtilizada).where(GaragemUtilizada.unidade_id == u1.id, GaragemUtilizada.excluido_em.is_(None)))] == [g2]  # g3 agora é a própria
    # correção que deixa veículo na garagem própria de quem a declarou alugada/cedida: o condômino volta a informar as garagens
    post(cb, "/veiculos", **V, placa="JKL4M56", garagem=g4)
    adm.post("/admin/garagem/corrigir", data={"unidade": str(u2.id), "garagem": g4})
    with SessionLocal() as db:
        n2 = db.get(Unidade, u2.id)
        assert (n2.garagem, n2.garagem_uso, n2.garagens_informadas_em) == (g4, None, None) and db.get(Unidade, u4.id).garagem == g2
    assert 'name="uso"' in cb.get("/morador/garagem").text and 'name="placa"' not in cb.get("/morador/garagem").text
    pg = ca.get("/morador/garagem").text
    assert "A administração alterou o vínculo de garagem" in pg and f"garagem {g3} (antes: {g1})" in pg and f"{r1} · Garagem {g3}" in pg
    linha = re.search(rf"<td>{re.escape(r1)}</td>.*?</tr>", adm.get("/admin/garagem").text, re.S).group(0)
    assert f"convenção: {g1}" in linha
    # menu interno: a Localização abre dentro de cada área (com o menu dela), sem sair para o site
    for c, area in ((ca, "morador"), (adm, "admin")):
        pg = c.get(f"/{area}/localizacao").text
        assert 'class="mapa"' in pg and 'class="submenu"' in pg and f'<a class="btn sec" href="/{area}/localizacao">Localização</a>' in pg
    assert TestClient(app, base_url="https://t").get("/morador/localizacao", follow_redirects=False).status_code == 303  # exige login
    assert 'class="submenu"' not in TestClient(app, base_url="https://t").get("/localizacao").text  # a página pública segue sem menu interno
    print("check_garagem ok")
finally:
    limpar()
