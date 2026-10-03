"""Fale Conosco: mensagem do condômino à administração (não é ocorrência), com tipo, texto obrigatório, declaração e captcha;
anexos opcionais só de imagem e 1 vídeo de até 30 s, verificados (assinatura, duração, antivírus) e gravados em
arquivos_fale_conosco com o sequencial do apartamento; a administração só lê, vendo quem enviou. Limpa o que cria:
docker exec condominio-app python scripts/check_fale_conosco.py"""
import re, struct, sys, time, uuid
sys.path.insert(0, "/app")
import mail
enviados = []
mail.enviar = lambda para, assunto, corpo, responder_para=None: (para == mail.MAIL_LOGS or enviados.append((para, assunto, corpo))) or True
registros = []  # o que iria para o histórico de auditoria: testes não gravam nele, só conferem (ação, detalhes)
mail._gravar_historico = lambda tipo, login, ip, acao, dados: registros.append((acao, dados))

from pathlib import Path
from urllib.parse import unquote
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import auth
import routers.admin as ra
from config import UPLOAD_DIR
from db import SessionLocal
from main import app
from models import BLOCO_TESTE, AdminUser, FaleConosco, FaleConoscoAnexo, Morador, Unidade
from termo import TERMO, TERMO_FALE_CONOSCO
from unidades_teste import unidades

A, B = "52998224725", "11144477735"
ADM, SEM = "teste.fale", "teste.semarea"  # usuários de administração do teste: com e sem a área Fale Conosco
PASTA = Path(UPLOAD_DIR) / "arquivos_fale_conosco"
PNG = ("foto.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64, "image/png")
JPG = ("foto.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 64, "image/jpeg")
TXT = "Sugiro bancos novos na praça do bloco."


def video(segundos, nome="video.mp4", moov=True):
    """MP4 mínimo: ftyp + moov/mvhd declarando a duração (é o que a verificação lê)."""
    mvhd = struct.pack(">I4sIIIII", 28, b"mvhd", 0, 0, 0, 1000, int(segundos * 1000))
    return nome, struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0) + (struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd if moov else b""), "video/mp4"


def captcha(certo=True):
    cp = auth.captcha_novo(); r = auth._captcha.loads(cp["token"])["r"]
    return {"captcha_token": cp["token"], "captcha": str(r if certo else r + 1), "declaracao": "sim"}


def ids_teste(db):
    return list(db.scalars(select(Unidade.id).where(Unidade.bloco == BLOCO_TESTE)))  # tudo o que houver nas unidades de teste é de teste


def limpar():
    with SessionLocal() as db:
        ids = ids_teste(db)
        for a in db.scalars(select(FaleConoscoAnexo).where(FaleConoscoAnexo.unidade_id.in_(ids))):
            (Path(UPLOAD_DIR) / a.arquivo).unlink(missing_ok=True)
        db.execute(delete(FaleConoscoAnexo).where(FaleConoscoAnexo.unidade_id.in_(ids)))
        db.execute(delete(FaleConosco).where(FaleConosco.unidade_id.in_(ids)))
        db.execute(delete(Morador).where(Morador.cpf.in_([A, B]))); db.execute(delete(AdminUser).where(AdminUser.login.in_([ADM, SEM]))); db.commit()


def cliente(tipo, id_):
    c = TestClient(app, base_url="https://t"); c.cookies.set(auth.COOKIE, auth.criar_sessao(tipo, str(id_))); return c


def enviar(c, arquivos=(), certo=True, **dados):
    """Envia pela área do condômino e devolve o destino do redirecionamento (com a mensagem de erro ou de confirmação)."""
    r = c.post("/morador/fale-conosco", data={"tipo": "sugestao", "texto": TXT, **captcha(certo), **dados},
               files=[("arquivos", a) for a in arquivos] or None, follow_redirects=False)
    assert r.status_code == 303, r.text
    return unquote(r.headers["location"])


def gravado():
    """(mensagens, anexos, arquivos em disco) das unidades de teste."""
    with SessionLocal() as db:
        ids = ids_teste(db)
        return (len(db.scalars(select(FaleConosco).where(FaleConosco.unidade_id.in_(ids))).all()),
                len(db.scalars(select(FaleConoscoAnexo).where(FaleConoscoAnexo.unidade_id.in_(ids))).all()),
                len([p for p in PASTA.glob("*") if p.name.startswith((f"BL_{BLOCO_TESTE}_", ".envio_"))]) if PASTA.is_dir() else 0)


def nao_lidas(c):
    m = re.search(r"Fale Conosco \((\d+)\)", c.get("/admin").text)
    return int(m.group(1)) if m else 0


limpar()
try:
    with SessionLocal() as db:
        u1, u2 = unidades(db, 2)
        for u, nome, cpf, email in ((u1, "Ana Fale", A, "ana@example.com"), (u2, "Bia Fale", B, "bia@example.com")):
            db.add(Morador(unidade_id=u.id, nome=nome, cpf=cpf, nascimento=auth.parse_data("1980-05-10"), email=email, telefone="91999990000", status="aprovado", termo_texto=TERMO))
        for login, areas in ((ADM, ["fale-conosco"]), (SEM, ["interfone"])):
            db.add(AdminUser(login=login, nome="Teste fale", senha_hash=auth.hash_senha(uuid.uuid4().hex), areas=areas, criado_por="teste"))
        db.commit()
        ida, idb = [db.scalar(select(Morador.id).where(Morador.cpf == c)) for c in (A, B)]
        adm, sem = [cliente("admin", db.scalar(select(AdminUser.id).where(AdminUser.login == login))) for login in (ADM, SEM)]
        mestre = cliente("admin", db.scalar(select(AdminUser.id).where(AdminUser.master)))  # vê o menu inteiro
        id1, id2, rotulo1 = u1.id, u2.id, u1.rotulo
        prefixo = f"BL_{u1.bloco}_AP_{u1.apto}_fale_"
    ca, cb, anonimo = cliente("morador", ida), cliente("morador", idb), TestClient(app, base_url="https://t")
    n0 = nao_lidas(adm)

    # 1. menu logo depois de Comunicados, nas duas áreas; aviso de que não é ocorrência, tipos e declaração legal na tela
    pg = ca.get("/morador/fale-conosco").text
    assert re.search(r'href="/morador/comunicados">Comunicados[^<]*</a>\s*<a class="btn sec" href="/morador/fale-conosco">Fale Conosco</a>', pg)
    assert re.search(r'href="/admin/comunicados">Comunicados</a>\s*<a class="btn sec" href="/admin/fale-conosco">Fale Conosco', mestre.get("/admin").text)
    assert "não é para registro de ocorrências" in pg and "não geram ocorrência" in pg and 'href="/morador/ocorrencias"' in pg
    assert all(t in pg for t in ("Sugestão", "Reclamação", "Ideia", "Conselho", "Elogio"))
    assert "art. 138" in pg and "não constitui registro de ocorrência" in pg and "quanto é" in pg and 'enctype="multipart/form-data"' in pg
    assert 'href="/morador/fale-conosco">Abrir' in ca.get("/morador").text  # cartão no início da área
    assert anonimo.get("/morador/fale-conosco", follow_redirects=False).status_code == 303  # exige login

    # 2. captcha, declaração, tipo e texto são obrigatórios
    assert "verificação incorreta" in enviar(ca, certo=False)
    assert registros[-1][0] == "Fale Conosco RECUSADO (captcha)" and "resposta errada" in registros[-1][1]["motivo"]  # o erro de captcha fica no histórico
    assert "aceitar a declaração" in enviar(ca, declaracao="")
    assert "Escolha o tipo" in enviar(ca, tipo="ocorrencia")
    assert "Escreva a mensagem" in enviar(ca, texto="   ")

    # 3. anexos: só imagem e vídeo; até 5 imagens e 1 vídeo de até 30 s; conteúdo conferido; antivírus. Recusa não grava nada
    assert "apenas imagens" in enviar(ca, [("doc.pdf", b"%PDF-1.4 teste", "application/pdf")])
    assert "Arquivo recusado" in enviar(ca, [("v.png", b"MZ\x90\x00 nao e png", "image/png")])
    assert "no máximo 5 imagens" in enviar(ca, [PNG] * 6)
    assert "no máximo 1 vídeo" in enviar(ca, [video(10), video(10, "outro.mov")])
    assert "no máximo 30 segundos" in enviar(ca, [PNG, video(45)])
    assert "Arquivo recusado" in enviar(ca, [video(10, moov=False)])  # sem cabeçalho de vídeo: não dá para conferir a duração
    assert "Arquivo recusado" in enviar(ca, [("v.mp4", PNG[1], "video/mp4")])  # imagem com extensão de vídeo
    orig = ra.escanear; ra.escanear = lambda caminho: (False, "Teste-Malware FOUND")  # simula detecção no caminho do upload
    try:
        assert "recusado pelo antivírus" in enviar(ca, [PNG])
        assert "recusado pelo antivírus" in enviar(ca, [video(10)])
    finally:
        ra.escanear = orig
    assert gravado() == (0, 0, 0), gravado()

    # 4. só texto: grava com declaração, IP e tipo; e-mail a contato@ e cópia ao condômino; fica no histórico
    assert "Mensagem enviada" in enviar(ca, tipo="elogio")
    with SessionLocal() as db:
        f = db.scalar(select(FaleConosco).where(FaleConosco.morador_id == ida))
        assert f.tipo == "elogio" and f.texto == TXT and f.criado_ip and f.termo_texto == TERMO_FALE_CONOSCO and f.termo_aceito_em and f.termo_ip and not f.lida_em
        fid = f.id
    time.sleep(0.3)
    assert {p for p, _, _ in enviados} == {mail.MAIL_CONTATO, "ana@example.com"}, enviados
    assert all("Fale Conosco" in a and "Elogio" in a and TXT in c and "não é ocorrência" in c for _, a, c in enviados)
    assert registros[-1][0] == "Fale Conosco: mensagem enviada" and registros[-1][1]["tipo"] == "Elogio"

    # 5. com anexos: BL_XX_AP_XXX_fale_DDMMYYYY_HHMMSS_<sequencial do apartamento>.<extensão>, em arquivos_fale_conosco
    assert "Mensagem enviada" in enviar(ca, [PNG, JPG, video(30.4, "Praça.MOV")], tipo="reclamacao")
    assert "Mensagem enviada" in enviar(ca, [JPG], tipo="ideia")
    assert "Mensagem enviada" in enviar(cb, [PNG], tipo="conselho")
    with SessionLocal() as db:
        an = db.scalars(select(FaleConoscoAnexo).where(FaleConoscoAnexo.unidade_id == id1).order_by(FaleConoscoAnexo.numero)).all()
        assert [a.numero for a in an] == [1, 2, 3, 4], [a.numero for a in an]  # o contador do apartamento continua no envio seguinte
        for a, ext in zip(an, ("png", "jpg", "mov", "jpg")):
            assert re.fullmatch(rf"arquivos_fale_conosco/{prefixo}\d{{8}}_\d{{6}}_{a.numero}\.{ext}", a.arquivo), a.arquivo
            assert (Path(UPLOAD_DIR) / a.arquivo).is_file()
        assert an[2].nome_original == "Praça.MOV"
        assert db.scalar(select(FaleConoscoAnexo.numero).where(FaleConoscoAnexo.unidade_id == id2)) == 1  # cada apartamento tem o seu contador
        aid = an[0].id
    assert not list(PASTA.glob(".envio_*"))  # nenhum arquivo provisório sobrando

    # 6. anexo: só o apartamento dono e a administração com a área; a área exige permissão
    assert ca.get(f"/morador/fale-conosco/anexo/{aid}").status_code == 200 and cb.get(f"/morador/fale-conosco/anexo/{aid}").status_code == 404
    assert adm.get(f"/admin/fale-conosco/anexo/{aid}").status_code == 200 and anonimo.get(f"/admin/fale-conosco/anexo/{aid}", follow_redirects=False).status_code == 303
    assert sem.get("/admin/fale-conosco").status_code == 403 and sem.get(f"/admin/fale-conosco/{fid}").status_code == 403 and sem.get(f"/admin/fale-conosco/anexo/{aid}").status_code == 403
    assert "Fale Conosco" not in sem.get("/admin").text  # menu esconde a área não liberada

    # 7. o condômino vê as mensagens do próprio apartamento; a administração lista com quem enviou, filtra e, ao abrir, marca como lida
    pg = ca.get("/morador/fale-conosco").text
    assert pg.count(TXT) == 3 and "Praça.MOV" in pg and "Reclamação" in pg and "ainda não lida" in pg
    assert cb.get("/morador/fale-conosco").text.count(TXT) == 1
    assert nao_lidas(adm) == n0 + 4
    lst = adm.get("/admin/fale-conosco").text
    assert "Ana Fale" in lst and "Bia Fale" in lst and rotulo1 in lst and "não é ocorrência" in lst.lower()
    assert "Bia Fale" in adm.get("/admin/fale-conosco?tipo=conselho").text and "Ana Fale" not in adm.get("/admin/fale-conosco?tipo=conselho").text
    # filtro por período do envio (dia de Belém, inclusive nas duas pontas); data inválida é ignorada
    from datetime import datetime, timedelta
    hoje = datetime.now(mail.FUSO).date(); ontem, amanha = hoje - timedelta(days=1), hoje + timedelta(days=1)
    assert "Ana Fale" in adm.get(f"/admin/fale-conosco?de={hoje}&ate={hoje}").text and "Ana Fale" in adm.get(f"/admin/fale-conosco?de={ontem}").text
    assert "Ana Fale" not in adm.get(f"/admin/fale-conosco?de={amanha}").text and "Ana Fale" not in adm.get(f"/admin/fale-conosco?ate={ontem}").text
    assert "Ana Fale" in adm.get("/admin/fale-conosco?de=31/12/2026&ate=xx").text
    assert f'name="de" value="{hoje}"' in adm.get(f"/admin/fale-conosco?de={hoje}").text and f"{hoje:%d/%m/%Y}" in lst  # a lista mostra data e hora do envio
    pg = adm.get(f"/admin/fale-conosco/{fid}").text
    assert TXT in pg and "Elogio" in pg and "Declaração de responsabilidade aceita" in pg and 'name="texto"' not in pg  # só leitura: sem resposta
    assert all(x in pg for x in ("Ana Fale", rotulo1, "529.982.247-25", "ana@example.com", "91999990000"))  # identificação de quem enviou
    assert registros[-1][0] == "Fale Conosco: mensagem lida pela administração"
    n_reg = len(registros); adm.get(f"/admin/fale-conosco/{fid}")
    assert len(registros) == n_reg  # reabrir não marca de novo
    with SessionLocal() as db:
        f = db.get(FaleConosco, fid); assert f.lida_em and f.lida_por == ADM
    assert nao_lidas(adm) == n0 + 3
    assert "Ana Fale" not in adm.get("/admin/fale-conosco?situacao=nao_lidas&tipo=elogio").text and "Ana Fale" in adm.get("/admin/fale-conosco?situacao=lidas&tipo=elogio").text
    assert "Lida pela administração em" in ca.get("/morador/fale-conosco").text
    assert adm.get(f"/admin/fale-conosco/{uuid.uuid4()}").status_code == 404

    # 8. imutável e de mão única: não existem rotas de resposta, edição ou exclusão
    assert adm.post(f"/admin/fale-conosco/{fid}/mensagem", data={"texto": "x"}).status_code in (404, 405)
    assert ca.post(f"/morador/fale-conosco/{fid}/excluir").status_code in (404, 405) and adm.post(f"/admin/fale-conosco/{fid}/excluir").status_code in (404, 405)
    print("check_fale_conosco ok")
finally:
    limpar()
