import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import String, func, select, text
from sqlalchemy.orm import Session

import auth
from config import UPLOAD_DIR
from db import get_db
from config import SITE_URL
from mail import FUSO, ip_de, notificar, registrar
from models import AREAS_ADMIN, AdminUser, Assembleia, CategoriaDocumento, Documento, Historico, Morador, Residente, Unidade
from antivirus import escanear
from routers.arquivos import servir_documento
from routers.morador import msg_ocupado, ocupante, validar_contato

log = logging.getLogger("uploads")
router = APIRouter(prefix="/admin")


def categorias(db: Session) -> list[str]:
    return [c.nome for c in db.scalars(select(CategoriaDocumento).order_by(CategoriaDocumento.nome))]
EXT_OK = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_TOTAL_MB = 100   # soma de todos os arquivos de um envio
BLOCO = 1024 * 1024  # gravação em blocos de 1 MB: nunca carrega o arquivo inteiro em memória
CONDOMINIO_CURTO = "Jardim Independência"
STATUS = ["pendente", "aprovado", "negado", "transferido"]
# transições: pendente -> aprovado|negado; aprovado -> negado ("habilitar novo registro").
# negado e transferido (condômino passou o acesso a um residente) são finais e liberam o apto.
TRANSICOES = {"pendente": {"aprovado", "negado"}, "aprovado": {"negado"}}
AVISO = {
    "aprovado": ("Acesso liberado", "Seu acesso à área do condômino foi liberado para {u}.\nEntre em {site}/morador/login com CPF e data de nascimento."),
    "negado": ("Solicitação não aprovada", "Sua solicitação de acesso para {u} não foi aprovada.\nEm caso de dúvida, procure a administração."),
    "encerrado": ("Acesso encerrado", "Seu acesso à área do condômino para {u} foi encerrado e o apartamento foi liberado para novo cadastro.\nEm caso de dúvida, procure a administração."),
}


def render(request: Request, nome: str, **ctx):
    from main import templates
    return templates.TemplateResponse(request, nome, {"sessao": request.state.sessao, "captcha": auth.captcha_novo(), **ctx})


def admin_dep(request: Request, sessao: dict = Depends(auth.exigir("admin")), db: Session = Depends(get_db)) -> AdminUser:
    """Carrega o usuário e bloqueia áreas não liberadas: /admin/<area>/... exige a área; /admin/usuarios exige master."""
    a = db.get(AdminUser, sessao["id"])
    if not a or a.excluido_em:
        raise HTTPException(status_code=303, headers={"Location": "/admin/sair"})
    partes = request.url.path.split("/")
    area = partes[2] if len(partes) > 2 else ""
    if area in ("usuarios", "historico") and not a.master:
        raise HTTPException(403, "Somente administradores mestres acessam usuários e histórico")
    if area in AREAS_ADMIN and not a.pode(area):
        raise HTTPException(403, "Área não liberada para o seu usuário")
    return a


@router.get("/login")
def login(request: Request, next: str = "/admin"):
    return render(request, "admin/login.html", next=next)


@router.post("/login")
def login_post(request: Request, login: str = Form(...), senha: str = Form(...), next: str = Form("/admin"),
               captcha: str = Form(""), captcha_token: str = Form(""), db: Session = Depends(get_db)):
    chave = f"admin:{ip_de(request)}"
    if auth.bloqueado(chave):
        return render(request, "admin/login.html", erro="Muitas tentativas. Aguarde 15 minutos.", next=next)
    if not auth.captcha_ok(captcha_token, captcha):
        auth.registrar_tentativa(chave)
        return render(request, "admin/login.html", erro="Resposta da conta de verificação incorreta. Tente novamente.", next=next)
    a = db.scalar(select(AdminUser).where(AdminUser.login == login.strip().lower(), AdminUser.excluido_em.is_(None)))
    if not a or not auth.verificar_senha(senha, a.senha_hash):
        auth.registrar_tentativa(chave)
        registrar("Login ADMIN recusado", request, login=login, senha_tentada=senha)
        return render(request, "admin/login.html", erro="Login ou senha incorretos.", next=next)
    auth.limpar_tentativas(chave)
    registrar("Login ADMIN realizado", request, login=a.login, senha="(correta; não registrada)")
    resp = RedirectResponse(next if next.startswith("/") else "/admin", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.criar_sessao("admin", str(a.id)), httponly=True, secure=True, samesite="lax",
                    max_age=auth.SESSAO_HORAS * 3600)
    return resp


@router.get("/sair")
def sair():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


@router.get("")
def painel(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    contagem = {s: n for s, n in db.execute(select(Morador.status, func.count()).group_by(Morador.status))}
    return render(request, "admin/painel.html", admin=admin, contagem=contagem,
                  aptos=db.scalar(select(func.count()).select_from(Unidade).where(Unidade.ativa, Unidade.apto != "")),
                  areas=db.scalar(select(func.count()).select_from(Unidade).where(Unidade.ativa, Unidade.apto == "")),
                  documentos=db.scalar(select(func.count()).select_from(Documento).where(Documento.excluido_em.is_(None))),
                  excluidos=db.scalar(select(func.count()).select_from(Documento).where(Documento.excluido_em.is_not(None))))


# ---- moradores ----
ORIGEM = {"site": "Solicitou no site", "admin": "Cadastrado pela administração", "transferencia": "Acesso transferido pelo condômino"}


@router.get("/moradores")
def moradores(request: Request, status: str = "", q: str = "", bloco: str = "", apto: str = "",
              admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Titulares (tabela morador) e residentes cadastrados pelo condômino (tabela residente), numa lista só."""
    apto_n = auth.so_digitos(apto).zfill(3)[-3:] if apto else ""

    def filtrar(stmt, modelo):
        if bloco:
            stmt = stmt.where(Unidade.bloco == bloco)
        if apto_n:
            stmt = stmt.where(Unidade.apto == apto_n)
        if q:
            stmt = stmt.where(modelo.nome.ilike(f"%{q}%") | modelo.cpf.contains(auth.so_digitos(q) or "§"))
        return stmt.order_by(Unidade.bloco, Unidade.apto, modelo.nome)

    linhas = []
    if status != "residente":
        stmt = filtrar(select(Morador).join(Unidade), Morador)
        if status:
            stmt = stmt.where(Morador.status == status)
        linhas += [dict(u=m.unidade, nome=m.nome, cpf=m.cpf_fmt, nasc=m.nascimento, email=m.email, tel=m.telefone,
                        papel="Titular do acesso", origem=ORIGEM.get(m.origem, m.origem), status=m.status, id=m.id,
                        quando=m.criado_em, registro=(m.decidido_por, m.decidido_em, m.decidido_ip) if m.decidido_em else None) for m in db.scalars(stmt)]
    if status in ("", "residente"):
        linhas += [dict(u=r.unidade, nome=r.nome, cpf=r.cpf_fmt, nasc=r.nascimento, email=r.email, tel=r.telefone,
                        papel=f"Residente · {r.tipo}", origem=f"Cadastrado pelo condômino {r.cadastrado_por}", status="", id=None,
                        quando=r.criado_em, registro=(r.cadastrado_por, r.criado_em, r.cadastrado_ip))
                   for r in db.scalars(filtrar(select(Residente).join(Unidade).where(Residente.excluido_em.is_(None)), Residente))]
    linhas.sort(key=lambda l: (l["u"].bloco, l["u"].apto, l["nome"]))
    from routers.financeiro import mapa_unidades
    mapa = mapa_unidades(db)
    return render(request, "admin/moradores.html", linhas=linhas, status=status, q=q, bloco=bloco, apto=apto_n,
                  mapa=mapa, mapa_form={**mapa, "PORTARIA": [], "ADMINISTRACAO": []})


@router.post("/moradores")
def morador_criar(request: Request, nome: str = Form(...), cpf: str = Form(...), nascimento: str = Form(...),
                  bloco: str = Form(...), apto: str = Form(...), email: str = Form(...), telefone: str = Form(...),
                  admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    cpf_d, nasc = auth.so_digitos(cpf), auth.parse_data(nascimento)
    apto = "" if bloco in ("PORTARIA", "ADMINISTRACAO") else auth.so_digitos(apto).zfill(3)[-3:]
    u = db.scalar(select(Unidade).where(Unidade.bloco == bloco, Unidade.apto == apto))
    if not auth.cpf_valido(cpf_d):
        raise HTTPException(400, "CPF inválido")
    if not nasc or not u:
        raise HTTPException(400, "Data ou unidade inválidos")
    if erro := validar_contato(email, telefone):
        raise HTTPException(400, erro)
    if ocup := ocupante(db, u.id):
        raise HTTPException(400, msg_ocupado(u, ocup))
    db.add(Morador(unidade_id=u.id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc, status="aprovado", origem="admin",
                   email=email.strip()[:160], telefone=telefone.strip()[:20],
                   decidido_em=datetime.now(timezone.utc), decidido_por=admin.login, decidido_ip=ip_de(request)))
    db.commit()
    registrar("Morador cadastrado pela administração (aprovado)", request, admin=admin.login, nome=nome.strip(), cpf=cpf_d, unidade=u.rotulo)
    return RedirectResponse("/admin/moradores", status_code=303)


@router.get("/moradores/{mid}")
def morador_ver(request: Request, mid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    m = db.get(Morador, mid) or (_ for _ in ()).throw(HTTPException(404))
    residentes = db.scalars(select(Residente).where(Residente.unidade_id == m.unidade_id).order_by(Residente.nome)).all()
    return render(request, "admin/morador.html", m=m, acoes=sorted(TRANSICOES.get(m.status, ())), residentes=residentes,
                  origem=ORIGEM.get(m.origem, m.origem))


@router.post("/moradores/{mid}/status")
def morador_status(request: Request, mid: uuid.UUID, status: str = Form(...), admin: AdminUser = Depends(admin_dep),
                   db: Session = Depends(get_db)):
    m = db.get(Morador, mid) or (_ for _ in ()).throw(HTTPException(404))
    if status not in TRANSICOES.get(m.status, ()):
        raise HTTPException(400, f"Não é possível passar de {m.status} para {status}")
    aviso = "encerrado" if (m.status, status) == ("aprovado", "negado") else status
    m.status, m.decidido_em, m.decidido_por, m.decidido_ip = status, datetime.now(timezone.utc), admin.login, ip_de(request)
    db.commit()
    assunto, corpo = AVISO[aviso]
    notificar(m.email, f"[{CONDOMINIO_CURTO}] {assunto}", corpo.format(u=m.unidade.rotulo, site=SITE_URL))
    registrar(f"Cadastro {status.upper()} pelo admin", request, admin=admin.login, nome=m.nome, cpf=m.cpf_fmt, unidade=m.unidade.rotulo, email=m.email)
    return RedirectResponse(f"/admin/moradores/{m.id}", status_code=303)


# ---- documentos ----
POR_PAGINA = 10


def _data(texto: str) -> date | None:
    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


@router.get("/documentos")
def documentos(request: Request, erro: str = "", ok: str = "", cad_de: str = "", cad_ate: str = "", comp_de: str = "", comp_ate: str = "",
               assembleia: str = "", categoria: str = "", situacao: str = "", pagina: int = 1, admin: AdminUser = Depends(admin_dep),
               db: Session = Depends(get_db)):
    """Lista com filtros (período de cadastro, de competência, assembleia, categoria, situação), total e paginação de POR_PAGINA.
    Sem filtro = todos os ativos; situacao=excluidos lista só os excluídos logicamente (só a administração vê), com os mesmos filtros."""
    cats = categorias(db)
    assembleias = db.scalars(select(Assembleia).where(Assembleia.excluido_em.is_(None)).order_by(Assembleia.abre_em.desc())).all()
    f = {"cad_de": _data(cad_de), "cad_ate": _data(cad_ate), "comp_de": _data(comp_de), "comp_ate": _data(comp_ate),
         "categoria": categoria if categoria in cats else "", "assembleia": assembleia if assembleia in ("avulso", *[str(a.id) for a in assembleias]) else "",
         "situacao": "excluidos" if situacao == "excluidos" else ""}
    cond = [Documento.excluido_em.is_not(None) if f["situacao"] else Documento.excluido_em.is_(None)]
    if f["cad_de"]:
        cond.append(Documento.criado_em >= datetime.combine(f["cad_de"], datetime.min.time(), FUSO))
    if f["cad_ate"]:
        cond.append(Documento.criado_em < datetime.combine(f["cad_ate"] + timedelta(days=1), datetime.min.time(), FUSO))
    if f["comp_de"]:
        cond.append(Documento.competencia >= f["comp_de"])
    if f["comp_ate"]:
        cond.append(Documento.competencia <= f["comp_ate"])
    if f["categoria"]:
        cond.append(Documento.categoria == f["categoria"])
    if f["assembleia"] == "avulso":
        cond.append(Documento.assembleia_id.is_(None))
    elif f["assembleia"]:
        cond.append(Documento.assembleia_id == uuid.UUID(f["assembleia"]))
    total = db.scalar(select(func.count()).select_from(Documento).where(*cond))
    paginas = max(1, -(-total // POR_PAGINA))
    pagina = min(max(1, pagina), paginas)
    ordem = Documento.excluido_em.desc() if f["situacao"] else Documento.criado_em.desc()
    docs = db.scalars(select(Documento).where(*cond).order_by(ordem).offset((pagina - 1) * POR_PAGINA).limit(POR_PAGINA)).all()
    filtro = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in f.items() if v}
    voltar = "/admin/documentos" + ("?" + urlencode({**filtro, "pagina": pagina}) if filtro or pagina > 1 else "")
    return render(request, "admin/documentos.html", documentos=docs, total=total, pagina=pagina, paginas=paginas, filtro=filtro, voltar=voltar,
                  excluidos=bool(f["situacao"]), categorias=cats, assembleias=assembleias, max_mb=MAX_TOTAL_MB, erro=erro, ok=ok, hoje=datetime.now(FUSO).date())


ASSINATURAS = {".pdf": (b"%PDF",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG\r\n\x1a\n",)}
# PDF com conteúdo ativo (onde vive a maior parte do malware em PDF): recusado
PDF_ATIVO = (b"/JavaScript", b"/JS ", b"/JS(", b"/JS<", b"/OpenAction", b"/AA ", b"/AA<", b"/Launch", b"/EmbeddedFile", b"/RichMedia")
ERRO_CONTEUDO = "Arquivo recusado: o conteúdo não corresponde ao tipo informado ou o PDF tem conteúdo ativo (JavaScript, ação automática ou anexo embutido)"
ERRO_UPLOAD = {-1: "O envio passou de {mb} MB no total", -2: ERRO_CONTEUDO + " ({nome})",
               -3: "Antivírus indisponível no momento; tente novamente em alguns minutos", -4: "Arquivo recusado pelo antivírus ({nome})"}


def conteudo_valido(destino: Path, ext: str) -> bool:
    """Checa a assinatura interna (magic bytes) e, em PDF, a ausência de conteúdo ativo."""
    with destino.open("rb") as f:
        inicio = f.read(16)
    if not any(inicio.startswith(a) for a in ASSINATURAS.get(ext, ())):
        log.warning("upload recusado (%s): assinatura interna %r não bate com %s", destino.name, inicio[:8], ext)
        return False
    if ext == ".pdf":
        with destino.open("rb") as f:
            while bloco := f.read(4 * BLOCO):
                if achado := next((t for t in PDF_ATIVO if t in bloco), None):
                    log.warning("upload recusado (%s): PDF com marcador %s", destino.name, achado.decode())
                    return False
    return True


async def _gravar_em_blocos(arquivo: UploadFile, destino: Path, restante: int) -> int:
    """Copia o upload para o disco em blocos. Devolve os bytes gravados; -1 se estourar o limite; -2 se o conteúdo
    for inválido (assinatura interna diferente da extensão ou PDF com conteúdo ativo). Em erro, o arquivo é apagado."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destino.open("wb") as f:
        while bloco := await arquivo.read(BLOCO):
            total += len(bloco)
            if total > restante:
                destino.unlink(missing_ok=True)
                return -1
            f.write(bloco)
    if not conteudo_valido(destino, destino.suffix.lower()):
        destino.unlink(missing_ok=True)
        return -2
    limpo, detalhe = escanear(destino)  # ClamAV; falha fechada se indisponível
    if not limpo:
        log.warning("upload recusado (%s): %s", destino.name, detalhe)
        destino.unlink(missing_ok=True)
        return -3 if detalhe == "antivírus indisponível" else -4
    return total


def _destino(voltar: str) -> str:
    return voltar if voltar.startswith("/admin/") else "/admin/documentos"


def _voltar_ok(destino: str, msg: str) -> RedirectResponse:
    """Volta à página de origem com a confirmação da ação (toda ação da administração dá retorno na tela)."""
    return RedirectResponse(f"{destino}{'&' if '?' in destino else '?'}ok={quote(msg)}", status_code=303)


def _voltar_erro(destino: str, msg: str) -> RedirectResponse:
    return RedirectResponse(f"{destino}{'&' if '?' in destino else '?'}erro={quote(msg)}", status_code=303)


def _competencia(texto: str) -> date | None:
    """Data de competência vinda do form (AAAA-MM-DD); vazio = hoje. None se inválida ou fora de faixa plausível."""
    try:
        d = date.fromisoformat(texto) if texto else datetime.now(FUSO).date()
    except ValueError:
        return None
    return d if 1900 <= d.year <= datetime.now(FUSO).year + 1 else None


@router.post("/documentos")
async def documento_enviar(request: Request, titulo: str = Form(""), categoria: str = Form(...), publico: str = Form(""),
                           assembleia_id: str = Form(""), voltar: str = Form(""), competencia: str = Form(""),
                           arquivos: list[UploadFile] = File(...), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Vários arquivos num envio só, até MAX_TOTAL_MB no total. Sem assembleia = documento avulso.
    `voltar`: página de origem (a página da assembleia envia daqui e volta para lá). `competencia`: data de assinatura/referência."""
    destino_ok = voltar if voltar.startswith("/admin/") else "/admin/documentos"

    def falha(msg):
        sep = "&" if "?" in destino_ok else "?"
        return RedirectResponse(f"{destino_ok}{sep}erro={msg}", status_code=303)
    arquivos = [a for a in arquivos if a.filename]
    if not arquivos:
        return falha("Selecione ao menos um arquivo")
    comp = _competencia(competencia)
    if not comp:
        return falha("Informe uma data de competência válida")
    if any(Path(a.filename).suffix.lower() not in EXT_OK for a in arquivos):
        return falha("Envie apenas PDF, JPG ou PNG")
    aid = None
    if assembleia_id:
        try:
            aid = uuid.UUID(assembleia_id)
        except ValueError:
            return falha("Assembleia inválida")
        if not db.get(Assembleia, aid):
            return falha("Assembleia não encontrada")
    cats = categorias(db)
    restante = MAX_TOTAL_MB * 1024 * 1024
    gravados: list[Path] = []
    novos: list[Documento] = []
    for a in arquivos:
        ext = Path(a.filename).suffix.lower()
        # nome do arquivo = UUID v7 do registro (ordenável no tempo, Postgres 18) + data/hora: sem duplicidade
        doc_id = db.scalar(text("select uuidv7()"))
        nome = f"documentos/{doc_id}_{datetime.now(FUSO):%d%m%Y_%H%M%S}{ext}"
        destino = Path(UPLOAD_DIR) / nome
        gravados.append(destino)
        n = await _gravar_em_blocos(a, destino, restante)
        if n < 0:
            for g in gravados:
                g.unlink(missing_ok=True)
            return falha(ERRO_UPLOAD[n].format(mb=MAX_TOTAL_MB, nome=a.filename))
        restante -= n
        base = titulo.strip()[:200] or Path(a.filename).stem[:200]
        t = base if len(arquivos) == 1 or not titulo.strip() else f"{base} ({len(novos) + 1})"
        novos.append(Documento(id=doc_id, titulo=t, categoria=categoria if categoria in cats else "Outros", arquivo=nome,
                               nome_original=Path(a.filename).name[:255], publico=bool(publico), assembleia_id=aid, competencia=comp,
                               enviado_por=admin.login, enviado_ip=ip_de(request)))
    db.add_all(novos)
    db.commit()
    registrar("Documentos enviados", request, admin=admin.login, quantidade=len(novos), assembleia=str(aid or "avulso"), competencia=comp.strftime("%d/%m/%Y"))
    return _voltar_ok(destino_ok, f"{len(novos)} documento(s) enviado(s)" + (" e publicado(s)" if publico else " (ainda não publicado(s))"))


@router.post("/documentos/{did}/publico")
def documento_publico(request: Request, did: uuid.UUID, publico: str = Form(""), voltar: str = Form(""), admin: AdminUser = Depends(admin_dep),
                      db: Session = Depends(get_db)):
    d = db.get(Documento, did) or (_ for _ in ()).throw(HTTPException(404))
    d.publico = publico == "1"
    db.commit()
    registrar("Documento " + ("publicado" if d.publico else "tornado privado"), request, admin=admin.login, titulo=d.titulo, arquivo=d.nome_original)
    return _voltar_ok(_destino(voltar), f"«{d.titulo}» " + ("publicado: já aparece para os condôminos" if d.publico else "tornado privado: não aparece mais para os condôminos"))


@router.post("/documentos/{did}/competencia")
def documento_competencia(request: Request, did: uuid.UUID, competencia: str = Form(""), justificativa: str = Form(""), voltar: str = Form(""),
                          admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Corrige a data de competência (assinatura/referência) de um documento. Exige justificativa; fica no histórico com a data anterior."""
    d = db.get(Documento, did) or (_ for _ in ()).throw(HTTPException(404))
    comp, just = _competencia(competencia), " ".join(justificativa.split())[:500]
    if not comp:
        return _voltar_erro(_destino(voltar), "Informe uma data de competência válida")
    if len(just) < 5:
        return _voltar_erro(_destino(voltar), "Informe a justificativa da alteração")
    anterior, d.competencia = d.competencia, comp
    db.commit()
    registrar("Competência do documento alterada", request, admin=admin.login, titulo=d.titulo, arquivo=d.nome_original,
              de=anterior.strftime("%d/%m/%Y"), para=comp.strftime("%d/%m/%Y"), justificativa=just)
    return _voltar_ok(_destino(voltar), f"Competência de «{d.titulo}» alterada de {anterior:%d/%m/%Y} para {comp:%d/%m/%Y}")


@router.post("/documentos/{did}/categoria")
def documento_categoria(request: Request, did: uuid.UUID, categoria: str = Form(""), justificativa: str = Form(""), voltar: str = Form(""),
                        admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Muda a categoria de um documento. Exige justificativa; fica no histórico com a categoria anterior."""
    d = db.get(Documento, did) or (_ for _ in ()).throw(HTTPException(404))
    just = " ".join(justificativa.split())[:500]
    if categoria not in categorias(db):
        return _voltar_erro(_destino(voltar), "Escolha uma categoria válida")
    if len(just) < 5:
        return _voltar_erro(_destino(voltar), "Informe a justificativa da alteração")
    anterior, d.categoria = d.categoria, categoria
    db.commit()
    registrar("Categoria do documento alterada", request, admin=admin.login, titulo=d.titulo, arquivo=d.nome_original,
              de=anterior, para=categoria, justificativa=just)
    return _voltar_ok(_destino(voltar), f"Categoria de «{d.titulo}» alterada de {anterior} para {categoria}")


@router.post("/documentos/{did}/excluir")
def documento_excluir(request: Request, did: uuid.UUID, justificativa: str = Form(""), voltar: str = Form(""), admin: AdminUser = Depends(admin_dep),
                      db: Session = Depends(get_db)):
    """Exclusão lógica com justificativa obrigatória: o arquivo e o registro ficam; sai da área do condômino e passa a aparecer
    na lista da administração com o filtro «Situação: excluídos»."""
    d = db.get(Documento, did)
    just = " ".join(justificativa.split())[:500]
    if d and not d.excluido_em:
        if len(just) < 5:
            return _voltar_erro(_destino(voltar), "Informe a justificativa da exclusão")
        d.publico, d.excluido_em, d.excluido_por, d.excluido_ip, d.excluido_motivo = False, datetime.now(timezone.utc), admin.login, ip_de(request), just
        db.commit()
        registrar("Documento excluído (lógico)", request, admin=admin.login, titulo=d.titulo, arquivo=d.nome_original, justificativa=just)
        return _voltar_ok(_destino(voltar), f"«{d.titulo}» excluído: continua acessível à administração no filtro «Situação: excluídos»")
    return RedirectResponse(_destino(voltar), status_code=303)


@router.get("/documentos/{did}")
def documento_baixar(did: str, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir_documento(db, did, apenas_publicos=False)


@router.post("/categorias")
def categoria_criar(request: Request, nome: str = Form(...), voltar: str = Form(""), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Nova categoria de documento (modal nas telas de envio). Precisa da área documentos: /admin/categorias não está em AREAS_ADMIN."""
    if not admin.pode("documentos"):
        raise HTTPException(403, "Área não liberada para o seu usuário")
    destino = voltar if voltar.startswith("/admin/") else "/admin/documentos"
    via_fetch = "application/json" in request.headers.get("accept", "")
    nome = " ".join(nome.split())[:80]
    if not nome:
        if via_fetch:
            return JSONResponse({"erro": "Informe o nome da categoria"}, status_code=400)
        return RedirectResponse(f"{destino}?erro=Informe+o+nome+da+categoria", status_code=303)
    existente = db.scalar(select(CategoriaDocumento).where(func.lower(CategoriaDocumento.nome) == nome.lower()))
    if existente:
        nome = existente.nome
    else:
        db.add(CategoriaDocumento(nome=nome))
        db.commit()
        registrar("Categoria de documento criada", request, admin=admin.login, categoria=nome)
    if via_fetch:  # o modal atualiza o select na hora, sem recarregar a página (não perde o form de envio)
        return JSONResponse({"nome": nome, "categorias": categorias(db)})
    return RedirectResponse(destino, status_code=303)


# ---- usuários da administração (só master; a checagem está em admin_dep) ----
@router.get("/usuarios")
def usuarios(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    todos = db.scalars(select(AdminUser).order_by(AdminUser.master.desc(), AdminUser.nome)).all()
    return render(request, "admin/usuarios.html", usuarios=[u for u in todos if not u.excluido_em],
                  desativados=[u for u in todos if u.excluido_em], areas=AREAS_ADMIN)


def _areas_do_form(form) -> list[str]:
    return [a for a in AREAS_ADMIN if form.get(f"area_{a}")]


@router.post("/usuarios")
async def usuario_criar(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    form = await request.form()
    login, nome, senha = str(form.get("login", "")).strip().lower(), str(form.get("nome", "")).strip()[:120], str(form.get("senha", ""))
    if not re.fullmatch(r"[a-z]+(\.[a-z]+)+", login) or len(login) > 60 or not nome or len(senha) < 8:
        raise HTTPException(400, "Login no formato nome.sobrenome (só letras minúsculas), nome e senha com 8+ caracteres são obrigatórios")
    if db.scalar(select(AdminUser).where(AdminUser.login == login)):
        raise HTTPException(400, "Já existe um usuário com este login (ativo ou desativado)")
    db.add(AdminUser(login=login, nome=nome, senha_hash=auth.hash_senha(senha), master=False, areas=_areas_do_form(form),
                     criado_por=admin.login, criado_ip=ip_de(request)))
    db.commit()
    registrar("Usuário da administração criado", request, por=admin.login, login=login, nome=nome, areas=", ".join(_areas_do_form(form)))
    return RedirectResponse("/admin/usuarios", status_code=303)


def _usuario_editavel(db: Session, uid: uuid.UUID) -> AdminUser:
    u = db.get(AdminUser, uid)
    if not u or u.master or u.excluido_em:
        raise HTTPException(404, "Usuário não encontrado, desativado ou mestre")
    return u


@router.post("/usuarios/{uid}/areas")
async def usuario_areas(request: Request, uid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    u.areas = _areas_do_form(await request.form())
    u.alterado_por, u.alterado_ip, u.alterado_em = admin.login, ip_de(request), datetime.now(timezone.utc)
    db.commit()
    registrar("Áreas de usuário alteradas", request, por=admin.login, login=u.login, areas=", ".join(u.areas))
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{uid}/senha")
def usuario_senha(request: Request, uid: uuid.UUID, senha: str = Form(...), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    if len(senha) < 8:
        raise HTTPException(400, "Senha com menos de 8 caracteres")
    u.senha_hash = auth.hash_senha(senha)
    u.alterado_por, u.alterado_ip, u.alterado_em = admin.login, ip_de(request), datetime.now(timezone.utc)
    db.commit()
    registrar("Senha de usuário redefinida", request, por=admin.login, login=u.login)
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{uid}/excluir")
def usuario_excluir(request: Request, uid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    u.excluido_em, u.excluido_por, u.excluido_ip = datetime.now(timezone.utc), admin.login, ip_de(request)  # desativação lógica
    db.commit()
    registrar("Usuário da administração desativado", request, por=admin.login, login=u.login)
    return RedirectResponse("/admin/usuarios", status_code=303)


# ---- histórico de auditoria (só master; checagem em admin_dep) ----
@router.get("/historico")
def historico(request: Request, de: str = "", ate: str = "", q: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Nada é listado sem período: o histórico cresce sem limite. Período = 00:00 de `de` até 23:59:59 de `ate` (hora de Belém)."""
    from datetime import time as _time
    from mail import FUSO
    primeira = db.scalar(select(func.min(Historico.quando)))
    total = db.scalar(select(func.count()).select_from(Historico))
    itens, erro = [], ""
    d_de, d_ate = auth.parse_data(de), auth.parse_data(ate)
    if de or ate:
        if not d_de or not d_ate:
            erro = "Informe as duas datas do período."
        elif d_ate < d_de:
            erro = "A data final é anterior à inicial."
        else:
            ini = datetime.combine(d_de, _time.min, tzinfo=FUSO)
            fim = datetime.combine(d_ate, _time.max, tzinfo=FUSO)
            stmt = select(Historico).where(Historico.quando >= ini, Historico.quando <= fim).order_by(Historico.quando.desc())
            if q:
                like = f"%{q}%"
                stmt = stmt.where(Historico.acao.ilike(like) | Historico.login.ilike(like) | Historico.ip.ilike(like) | Historico.detalhe.cast(String).ilike(like))
            itens = db.scalars(stmt.limit(500)).all()
    return render(request, "admin/historico.html", itens=itens, q=q, de=de, ate=ate, erro=erro, total=total,
                  primeira=primeira.astimezone(FUSO) if primeira else None, filtrado=bool(de or ate) and not erro)


# ---- senha do admin ----
@router.post("/senha")
def trocar_senha(request: Request, atual: str = Form(...), nova: str = Form(...), admin: AdminUser = Depends(admin_dep),
                 db: Session = Depends(get_db)):
    if not auth.verificar_senha(atual, admin.senha_hash) or len(nova) < 8:
        raise HTTPException(400, "Senha atual incorreta ou nova senha com menos de 8 caracteres")
    admin.senha_hash = auth.hash_senha(nova)
    db.commit()
    registrar("Senha do próprio usuário alterada", request, login=admin.login)
    return RedirectResponse("/admin", status_code=303)
