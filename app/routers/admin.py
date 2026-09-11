import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import auth
from config import UPLOAD_DIR
from db import get_db
from config import SITE_URL
from mail import ip_de, notificar, registrar
from models import AREAS_ADMIN, AdminUser, Assembleia, Documento, Morador, Residente, Unidade
from routers.arquivos import servir_documento
from routers.morador import msg_ocupado, ocupante, validar_contato

router = APIRouter(prefix="/admin")
CATEGORIAS = ["Convenção", "Regimento interno", "Atas de assembleia", "Balancetes", "Comunicados", "Outros"]
EXT_OK = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_TOTAL_MB = 100   # soma de todos os arquivos de um envio
BLOCO = 1024 * 1024  # gravação em blocos de 1 MB: nunca carrega o arquivo inteiro em memória
CONDOMINIO_CURTO = "Jardim Independência"
STATUS = ["pendente", "aprovado", "negado"]
# transições: pendente -> aprovado|negado; aprovado -> negado ("habilitar novo registro"). negado é final e libera o apto.
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
    if not a:
        raise HTTPException(status_code=303, headers={"Location": "/admin/sair"})
    partes = request.url.path.split("/")
    area = partes[2] if len(partes) > 2 else ""
    if area == "usuarios" and not a.master:
        raise HTTPException(403, "Somente administradores mestres gerenciam usuários")
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
        return render(request, "admin/login.html", erro="Resposta da conta de verificação incorreta. Tente novamente.", next=next)
    a = db.scalar(select(AdminUser).where(AdminUser.login == login.strip().lower()))
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
                  documentos=db.scalar(select(func.count()).select_from(Documento)))


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
                   for r in db.scalars(filtrar(select(Residente).join(Unidade), Residente))]
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
    if not auth.cpf_valido(cpf_d) or not nasc or not u:
        raise HTTPException(400, "CPF, data ou unidade inválidos")
    if erro := validar_contato(email, telefone):
        raise HTTPException(400, erro)
    if ocup := ocupante(db, u.id):
        raise HTTPException(400, msg_ocupado(u, ocup))
    db.add(Morador(unidade_id=u.id, nome=nome.strip()[:120], cpf=cpf_d, nascimento=nasc, status="aprovado", origem="admin",
                   email=email.strip()[:160], telefone=telefone.strip()[:20],
                   decidido_em=datetime.now(timezone.utc), decidido_por=admin.login, decidido_ip=ip_de(request)))
    db.commit()
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
@router.get("/documentos")
def documentos(request: Request, erro: str = "", admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    docs = db.scalars(select(Documento).order_by(Documento.criado_em.desc())).all()
    assembleias = db.scalars(select(Assembleia).order_by(Assembleia.abre_em.desc())).all()
    return render(request, "admin/documentos.html", documentos=docs, categorias=CATEGORIAS, assembleias=assembleias,
                  max_mb=MAX_TOTAL_MB, erro=erro)


async def _gravar_em_blocos(arquivo: UploadFile, destino: Path, restante: int) -> int:
    """Copia o upload para o disco em blocos; devolve os bytes gravados ou -1 se estourar o limite."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destino.open("wb") as f:
        while bloco := await arquivo.read(BLOCO):
            total += len(bloco)
            if total > restante:
                return -1
            f.write(bloco)
    return total


@router.post("/documentos")
async def documento_enviar(request: Request, titulo: str = Form(""), categoria: str = Form(...), publico: str = Form(""),
                           assembleia_id: str = Form(""), voltar: str = Form(""), arquivos: list[UploadFile] = File(...),
                           admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Vários arquivos num envio só, até MAX_TOTAL_MB no total. Sem assembleia = documento avulso.
    `voltar`: página de origem (a página da assembleia envia daqui e volta para lá)."""
    destino_ok = voltar if voltar.startswith("/admin/") else "/admin/documentos"

    def falha(msg):
        sep = "&" if "?" in destino_ok else "?"
        return RedirectResponse(f"{destino_ok}{sep}erro={msg}", status_code=303)
    arquivos = [a for a in arquivos if a.filename]
    if not arquivos:
        return falha("Selecione ao menos um arquivo")
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
    restante = MAX_TOTAL_MB * 1024 * 1024
    gravados: list[Path] = []
    novos: list[Documento] = []
    for a in arquivos:
        ext = Path(a.filename).suffix.lower()
        nome = f"documentos/{uuid.uuid4()}{ext}"
        destino = Path(UPLOAD_DIR) / nome
        gravados.append(destino)
        n = await _gravar_em_blocos(a, destino, restante)
        if n < 0:
            for g in gravados:
                g.unlink(missing_ok=True)
            return falha(f"O envio passou de {MAX_TOTAL_MB} MB no total")
        restante -= n
        base = titulo.strip()[:200] or Path(a.filename).stem[:200]
        t = base if len(arquivos) == 1 or not titulo.strip() else f"{base} ({len(novos) + 1})"
        novos.append(Documento(titulo=t, categoria=categoria if categoria in CATEGORIAS else "Outros", arquivo=nome,
                               nome_original=Path(a.filename).name[:255], publico=bool(publico), assembleia_id=aid))
    db.add_all(novos)
    db.commit()
    registrar("Documentos enviados", request, admin=admin.login, quantidade=len(novos), assembleia=str(aid or "avulso"))
    return RedirectResponse(destino_ok, status_code=303)


@router.post("/documentos/{did}/publico")
def documento_publico(did: uuid.UUID, publico: str = Form(""), admin: AdminUser = Depends(admin_dep),
                      db: Session = Depends(get_db)):
    d = db.get(Documento, did) or (_ for _ in ()).throw(HTTPException(404))
    d.publico = publico == "1"
    db.commit()
    return RedirectResponse("/admin/documentos", status_code=303)


@router.post("/documentos/{did}/excluir")
def documento_excluir(did: uuid.UUID, voltar: str = Form(""), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    d = db.get(Documento, did)
    if d:
        (Path(UPLOAD_DIR) / d.arquivo).unlink(missing_ok=True)
        db.delete(d)
        db.commit()
    return RedirectResponse(voltar if voltar.startswith("/admin/") else "/admin/documentos", status_code=303)


@router.get("/documentos/{did}")
def documento_baixar(did: str, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    return servir_documento(db, did, apenas_publicos=False)


# ---- usuários da administração (só master; a checagem está em admin_dep) ----
@router.get("/usuarios")
def usuarios(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    lista = db.scalars(select(AdminUser).order_by(AdminUser.master.desc(), AdminUser.nome)).all()
    return render(request, "admin/usuarios.html", usuarios=lista, areas=AREAS_ADMIN)


def _areas_do_form(form) -> list[str]:
    return [a for a in AREAS_ADMIN if form.get(f"area_{a}")]


@router.post("/usuarios")
async def usuario_criar(request: Request, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    form = await request.form()
    login, nome, senha = str(form.get("login", "")).strip().lower(), str(form.get("nome", "")).strip()[:120], str(form.get("senha", ""))
    if not re.fullmatch(r"[a-z]+(\.[a-z]+)+", login) or len(login) > 60 or not nome or len(senha) < 8:
        raise HTTPException(400, "Login no formato nome.sobrenome (só letras minúsculas), nome e senha com 8+ caracteres são obrigatórios")
    if db.scalar(select(AdminUser).where(AdminUser.login == login)):
        raise HTTPException(400, "Já existe um usuário com este login")
    db.add(AdminUser(login=login, nome=nome, senha_hash=auth.hash_senha(senha), master=False, areas=_areas_do_form(form)))
    db.commit()
    registrar("Usuário da administração criado", request, por=admin.login, login=login, nome=nome, areas=", ".join(_areas_do_form(form)))
    return RedirectResponse("/admin/usuarios", status_code=303)


def _usuario_editavel(db: Session, uid: uuid.UUID) -> AdminUser:
    u = db.get(AdminUser, uid)
    if not u or u.master:
        raise HTTPException(404, "Usuário não encontrado ou é mestre")
    return u


@router.post("/usuarios/{uid}/areas")
async def usuario_areas(request: Request, uid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    u.areas = _areas_do_form(await request.form())
    db.commit()
    registrar("Áreas de usuário alteradas", request, por=admin.login, login=u.login, areas=", ".join(u.areas))
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{uid}/senha")
def usuario_senha(request: Request, uid: uuid.UUID, senha: str = Form(...), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    if len(senha) < 8:
        raise HTTPException(400, "Senha com menos de 8 caracteres")
    u.senha_hash = auth.hash_senha(senha)
    db.commit()
    registrar("Senha de usuário redefinida", request, por=admin.login, login=u.login)
    return RedirectResponse("/admin/usuarios", status_code=303)


@router.post("/usuarios/{uid}/excluir")
def usuario_excluir(request: Request, uid: uuid.UUID, admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    u = _usuario_editavel(db, uid)
    registrar("Usuário da administração excluído", request, por=admin.login, login=u.login)
    db.delete(u)
    db.commit()
    return RedirectResponse("/admin/usuarios", status_code=303)


# ---- senha do admin ----
@router.post("/senha")
def trocar_senha(request: Request, atual: str = Form(...), nova: str = Form(...), admin: AdminUser = Depends(admin_dep),
                 db: Session = Depends(get_db)):
    if not auth.verificar_senha(atual, admin.senha_hash) or len(nova) < 8:
        raise HTTPException(400, "Senha atual incorreta ou nova senha com menos de 8 caracteres")
    admin.senha_hash = auth.hash_senha(nova)
    db.commit()
    return RedirectResponse("/admin", status_code=303)
