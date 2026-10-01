"""Animais de Estimação: cadastro pelo condômino (nome, tipo, raça e foto opcionais; vários por apartamento), foto restrita ao
próprio apartamento e à administração, consulta da administração com contagem e filtros.
Limpa o que cria (inclusive as fotos): docker exec condominio-app python scripts/check_animais.py"""
import re
import sys
import uuid
from pathlib import Path
sys.path.insert(0, "/app")
import mail, apns
mail.enviar = lambda *a, **k: True
mail._gravar_historico = lambda *a, **k: None  # testes não entram no histórico de auditoria
apns.notificar = lambda *a, **k: None  # teste nunca manda push aos condôminos

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
from models import BLOCO_TESTE, AdminUser, Animal, Morador, Unidade
from termo import TERMO
from unidades_teste import unidades

A, B = "52998224725", "11144477735"
ADM, SEM = "teste.animais", "teste.semarea"  # usuários de administração do teste: com e sem a área Animais de Estimação
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 64


def limpar():
    with SessionLocal() as db:
        ids = list(db.scalars(select(Unidade.id).where(Unidade.bloco == BLOCO_TESTE)))  # tudo o que houver nas unidades de teste é de teste
        for a in db.scalars(select(Animal).where(Animal.unidade_id.in_(ids))):
            if a.foto:
                (Path(UPLOAD_DIR) / a.foto).unlink(missing_ok=True)
        db.execute(delete(Animal).where(Animal.unidade_id.in_(ids)))
        for u in db.scalars(select(Unidade).where(Unidade.id.in_(ids))):
            u.sem_animais_em = u.sem_animais_por = None
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.execute(delete(AdminUser).where(AdminUser.login.in_([ADM, SEM]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


def post(c, foto=None, **dados):
    """Cadastra pela área do condômino e devolve a página resultante (com a mensagem de erro ou de confirmação)."""
    return c.post("/morador/animais", data=dados, files={"foto": foto or ("", b"")}).text


def editar(c, aid, foto=None, **dados):
    """Edita pela área do condômino e devolve a página resultante."""
    return c.post(f"/morador/animais/{aid}/editar", data=dados, files={"foto": foto or ("", b"")}).text


def totais(pg):
    """(unidades, animais) listados na página da administração, conforme o filtro."""
    return tuple(int(n) for n in re.search(r"Unidades \((\d+)\).*?Animais \((\d+)\)", pg, re.S).groups())


def geral(c):
    """(unidades com animais, que informaram não possuir, que ainda não informaram) do resumo da administração."""
    return tuple(int(n) for n in re.search(r"<strong>(\d+)</strong> unidade\(s\) com animais registrados · <strong>(\d+)</strong> informaram não possuir · "
                                           r"<strong>(\d+)</strong> ainda não informaram", c.get("/admin/animais").text).groups())


limpar()
try:
    with SessionLocal() as db:
        u1, u2 = unidades(db, 2)
        r1, r2 = u1.rotulo, u2.rotulo
        for u, nome, cpf in ((u1, "Ana Animais", A), (u2, "Bia Animais", B)):
            db.add(Morador(unidade_id=u.id, nome=nome, cpf=cpf, nascimento=auth.parse_data("1980-05-10"), email="a@example.com", telefone="91999990000", status="aprovado", termo_texto=TERMO))
        for login, areas in ((ADM, ["garagem", "animais"]), (SEM, ["interfone"])):
            db.add(AdminUser(login=login, nome="Teste animais", senha_hash=auth.hash_senha(uuid.uuid4().hex), areas=areas, criado_por="teste"))
        db.commit()
        ida, idb = [db.scalar(select(Morador.id).where(Morador.cpf == c)) for c in (A, B)]
        adm, sem = [cliente("admin", db.scalar(select(AdminUser.id).where(AdminUser.login == login))) for login in (ADM, SEM)]
    ca, cb, anonimo = cliente("morador", ida), cliente("morador", idb), TestClient(app, base_url="https://t")
    un0, an0 = totais(adm.get("/admin/animais").text)

    # 1. menu logo depois de Garagem e Veículos, nas duas áreas; sem animal, só o formulário
    pg = ca.get("/morador/animais").text
    for area, html in (("morador", pg), ("admin", adm.get("/admin/animais").text)):
        assert re.search(rf'href="/{area}/garagem">Garagem e Veículos</a>\s*<a class="btn sec" href="/{area}/animais">Animais de Estimação</a>', html)
    assert "Nenhum animal cadastrado" in pg and 'name="nome"' in pg and 'name="foto"' in pg and 'enctype="multipart/form-data"' in pg
    assert 'href="/morador/animais">Abrir' in ca.get("/morador").text  # cartão no início da área
    assert anonimo.get("/morador/animais", follow_redirects=False).status_code == 303  # exige login

    # 2. validação: nome e tipo obrigatórios; foto só JPG ou PNG de verdade
    assert "Informe o nome" in post(ca, nome="  ", tipo="Gato")
    assert "Escolha o tipo" in post(ca, nome="Mimiteste", tipo="Dragão")
    assert "JPG ou PNG" in post(ca, nome="Mimiteste", tipo="Gato", foto=("mimi.gif", b"GIF89a"))
    assert "não é uma imagem JPG ou PNG válida" in post(ca, nome="Mimiteste", tipo="Gato", foto=("mimi.png", b"<html>nada de imagem</html>"))
    with SessionLocal() as db:
        assert not db.scalars(select(Animal).where(Animal.unidade_id == u1.id)).all()
    assert not list((Path(UPLOAD_DIR) / "imagens_animais").glob(".envio_*"))  # foto recusada não deixa arquivo provisório

    # 3. cadastro: raça e foto opcionais; o formulário continua disponível e aceita vários animais
    pg = post(ca, nome="  Mimiteste  ", tipo="Gato"); assert "Mimiteste cadastrad" in pg and "<td>Mimiteste</td>" in pg and 'name="nome"' in pg
    pg = post(ca, nome="Rexteste", tipo="Cachorro", raca="Vira-lata", foto=("rex.PNG", PNG)); assert "<td>Rexteste</td>" in pg and "Vira-lata" in pg
    post(ca, nome="Louroteste", tipo="Pássaro", raca="Papagaio", foto=("louro.jpeg", JPG))
    assert "já está cadastrad" in post(ca, nome="rexteste", tipo="Cachorro")  # envio em dobro não duplica
    # sem foto, como o navegador envia: o campo de arquivo vem com nome vazio
    corpo = "".join(f'--x\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in (("nome", "Thorteste"), ("tipo", "Cachorro"), ("raca", "Labradorteste")))
    corpo += '--x\r\nContent-Disposition: form-data; name="foto"; filename=""\r\nContent-Type: application/octet-stream\r\n\r\n\r\n--x--\r\n'
    assert "Thorteste cadastrad" in cb.post("/morador/animais", content=corpo.encode(), headers={"Content-Type": "multipart/form-data; boundary=x"}).text
    post(cb, nome="Ninateste", tipo="Gato", foto=("nina.jpg", JPG))
    with SessionLocal() as db:
        # o contador é de cada apartamento: o 2º animal da outra unidade também é o nº 2, com o bloco e o apto dela no nome
        assert db.scalar(select(Animal.foto).where(Animal.unidade_id == u2.id, Animal.nome == "Ninateste")) == f"imagens_animais/BL_{u2.bloco}_AP_{u2.apto}_animal_2.jpg"
        meus = {a.nome: a for a in db.scalars(select(Animal).where(Animal.unidade_id == u1.id, Animal.excluido_em.is_(None)))}
        assert sorted(meus) == ["Louroteste", "Mimiteste", "Rexteste"] and meus["Mimiteste"].raca is None and meus["Mimiteste"].foto is None
        # foto em imagens_animais, com o nome BL_XX_AP_XXX_animal_<sequencial do animal no apartamento>.<extensão>
        base = f"imagens_animais/BL_{u1.bloco}_AP_{u1.apto}_animal_"
        assert [meus[n].numero for n in ("Mimiteste", "Rexteste", "Louroteste")] == [1, 2, 3] and (meus["Rexteste"].foto, meus["Louroteste"].foto) == (base + "2.png", base + "3.jpeg")
        assert (Path(UPLOAD_DIR) / meus["Rexteste"].foto).read_bytes() == PNG and not list((Path(UPLOAD_DIR) / "imagens_animais").glob(".envio_*"))
        assert "Ana Animais" in meus["Rexteste"].cadastrado_por
        rex, mimi, louro = meus["Rexteste"].id, meus["Mimiteste"].id, meus["Louroteste"].id
    pg = ca.get("/morador/animais").text
    assert f'src="/morador/animais/{rex}/foto"' in pg and f"/morador/animais/{mimi}/foto" not in pg
    assert f'href="/morador/animais?editar={rex}#form">Editar</a>' in pg and f'action="/morador/animais/{rex}/excluir"' in pg and ">Excluir</button>" in pg

    # 4. foto: só o próprio apartamento e a administração com a área; quem não tem foto devolve 404
    r = ca.get(f"/morador/animais/{rex}/foto"); assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert ca.get(f"/morador/animais/{louro}/foto").headers["content-type"] == "image/jpeg"
    assert ca.get(f"/morador/animais/{mimi}/foto").status_code == 404
    assert cb.get(f"/morador/animais/{rex}/foto").status_code == 404
    assert anonimo.get(f"/morador/animais/{rex}/foto", follow_redirects=False).status_code == 303
    assert anonimo.get(f"/admin/animais/{rex}/foto", follow_redirects=False).status_code == 303
    assert adm.get(f"/admin/animais/{rex}/foto").content == PNG
    assert sem.get(f"/admin/animais/{rex}/foto").status_code == 403 and sem.get("/admin/animais").status_code == 403

    # 5. administração: quantas e quais unidades registraram animais, os registros e os filtros
    pg = adm.get("/admin/animais").text
    assert totais(pg) == (un0 + 2, an0 + 5)
    linha = re.search(rf"<td>{re.escape(r1)}</td>\s*<td><strong>3</strong>.*?</tr>", pg, re.S).group(0)
    assert all(n in linha for n in ("Mimiteste", "Rexteste", "Louroteste")) and re.search(rf"<td>{re.escape(r2)}</td>\s*<td><strong>2</strong>", pg)
    assert f'src="/admin/animais/{rex}/foto"' in pg and "Vira-lata" in pg and "Ana Animais" in pg
    pg = adm.get("/admin/animais?tipo=Cachorro").text; assert ">Rexteste<" in pg and ">Thorteste<" in pg and ">Mimiteste<" not in pg
    pg = adm.get("/admin/animais?q=LABRADORTESTE").text; assert ">Thorteste<" in pg and ">Rexteste<" not in pg and totais(pg) == (1, 1)  # busca por nome ou raça
    pg = adm.get(f"/admin/animais?bloco={u1.bloco}&q=mimiteste").text; assert ">Mimiteste<" in pg and ">Rexteste<" not in pg
    assert totais(adm.get("/admin/animais?q=nenhum-animal-com-este-nome").text) == (0, 0)

    # 6. remoção lógica: só do próprio apartamento; some da tela e da administração, fica no banco, e a foto deixa de ser servida
    assert cb.post(f"/morador/animais/{rex}/excluir").status_code == 404
    pg = ca.post(f"/morador/animais/{rex}/excluir").text; assert "<td>Rexteste</td>" not in pg and "<td>Mimiteste</td>" in pg
    with SessionLocal() as db:
        x = db.get(Animal, rex); assert x.excluido_em and "Ana Animais" in x.excluido_por
    assert ca.get(f"/morador/animais/{rex}/foto").status_code == 404 and adm.get(f"/admin/animais/{rex}/foto").status_code == 404
    assert totais(adm.get("/admin/animais").text) == (un0 + 2, an0 + 4)
    # removido, pode ser cadastrado de novo: recebe o próximo número e a foto antiga não é sobrescrita
    assert "<td>Rexteste</td>" in post(ca, nome="Rexteste", tipo="Cachorro", foto=("novo.jpg", JPG))
    assert (Path(UPLOAD_DIR) / (base + "2.png")).read_bytes() == PNG and (Path(UPLOAD_DIR) / (base + "4.jpg")).read_bytes() == JPG

    # 7. edição do animal como um todo: só do próprio apartamento; o formulário vem preenchido e os erros voltam para ele
    def gravado():
        with SessionLocal() as db:
            x = db.get(Animal, mimi); return x.nome, x.tipo, x.raca, x.foto, x.numero
    assert cb.post(f"/morador/animais/{mimi}/editar", data=dict(nome="Outro", tipo="Gato")).status_code == 404
    assert ca.post(f"/morador/animais/{rex}/editar", data=dict(nome="Outro", tipo="Gato")).status_code == 404  # animal já excluído
    pg = ca.get(f"/morador/animais?editar={mimi}").text
    assert f'action="/morador/animais/{mimi}/editar"' in pg and 'value="Mimiteste"' in pg and "<option selected>Gato</option>" in pg and "Salvar alterações" in pg
    assert 'name="sem_foto"' not in pg  # ainda sem foto: não há o que excluir
    pg = editar(ca, mimi, nome=" ", tipo="Gato"); assert "Informe o nome" in pg and f'action="/morador/animais/{mimi}/editar"' in pg
    assert "já está cadastrad" in editar(ca, mimi, nome="LOUROTESTE", tipo="Pássaro") and gravado() == ("Mimiteste", "Gato", None, None, 1)
    # altera tipo e raça e inclui a foto: o número do animal não muda e é ele que dá nome à foto
    pg = editar(ca, mimi, nome="Mimiteste", tipo="Outro", raca="Siamesteste", foto=("m.png", PNG))
    assert "Mimiteste alterado" in pg and "Siamesteste" in pg and f'src="/morador/animais/{mimi}/foto?v=' in pg
    assert gravado() == ("Mimiteste", "Outro", "Siamesteste", base + "1.png", 1) and 'name="sem_foto"' in ca.get(f"/morador/animais?editar={mimi}").text
    with SessionLocal() as db:
        assert "Ana Animais" in db.get(Animal, mimi).alterado_por
    assert "alterado por Ana Animais" in adm.get(f"/admin/animais?bloco={u1.bloco}&q=mimiteste").text
    # troca da foto: a nova toma o lugar da antiga (uma só por animal); foto inválida não mexe na atual
    editar(ca, mimi, nome="Mimiteste", tipo="Outro", raca="Siamesteste", foto=("m.jpg", JPG))
    assert gravado()[3] == base + "1.jpg" and ca.get(f"/morador/animais/{mimi}/foto").content == JPG and not (Path(UPLOAD_DIR) / (base + "1.png")).exists()
    assert "não é uma imagem JPG ou PNG válida" in editar(ca, mimi, nome="Mimiteste", tipo="Outro", raca="Siamesteste", foto=("m.png", b"<html>"))
    assert gravado() == ("Mimiteste", "Outro", "Siamesteste", base + "1.jpg", 1) and (Path(UPLOAD_DIR) / (base + "1.jpg")).read_bytes() == JPG
    editar(ca, mimi, nome="Mimiteste", tipo="Outro", raca="Siamesteste", sem_foto="1", foto=("outra.jpg", JPG + b"2"))  # mesma extensão; foto nova vale mais que "excluir a foto"
    assert gravado()[3] == base + "1.jpg" and (Path(UPLOAD_DIR) / (base + "1.jpg")).read_bytes() == JPG + b"2"
    assert not list((Path(UPLOAD_DIR) / "imagens_animais").glob(".envio_*"))
    # exclusão só da foto; apagar a raça
    editar(ca, mimi, nome="Mimiteste", tipo="Outro", raca="", sem_foto="1")
    assert gravado() == ("Mimiteste", "Outro", None, None, 1) and not (Path(UPLOAD_DIR) / (base + "1.jpg")).exists()

    # 8. unidade informa que não possui animais: só sem animal cadastrado; pode desfazer; cadastrar um animal apaga a declaração
    assert "Há animais cadastrados" in ca.post("/morador/animais/nenhum").text
    with SessionLocal() as db:
        for aid in db.scalars(select(Animal.id).where(Animal.unidade_id == u2.id, Animal.excluido_em.is_(None))).all():
            cb.post(f"/morador/animais/{aid}/excluir")
    pg = cb.get("/morador/animais").text
    assert "Esta unidade não possui animais de estimação</button>" in pg and 'value="desfazer"' not in pg
    com, sem0, pend0 = geral(adm)
    pg = cb.post("/morador/animais/nenhum").text
    assert "informou que não possui animais de estimação (Bia Animais" in pg and 'value="desfazer"' in pg and 'name="nome"' in pg
    assert geral(adm) == (com, sem0 + 1, pend0 - 1)
    pg = adm.get(f"/admin/animais?situacao=sem&bloco={u2.bloco}").text; assert f"<td>{r2}</td>" in pg and "informado por <strong>Bia Animais" in pg
    assert f"<td>{r2}</td>" not in adm.get(f"/admin/animais?situacao=pendente&bloco={u2.bloco}").text
    pg = cb.post("/morador/animais/nenhum", data={"acao": "desfazer"}).text
    assert "Esta unidade não possui animais de estimação</button>" in pg and geral(adm) == (com, sem0, pend0)
    assert f"<td>{r2}</td>" in adm.get(f"/admin/animais?situacao=pendente&bloco={u2.bloco}").text
    cb.post("/morador/animais/nenhum"); post(cb, nome="Ninateste", tipo="Gato")
    with SessionLocal() as db:
        assert db.get(Unidade, u2.id).sem_animais_em is None
    assert geral(adm) == (com + 1, sem0, pend0 - 1)
    print("check_animais ok")
finally:
    limpar()
