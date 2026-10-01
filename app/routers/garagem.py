"""Garagem e Veículos. Condômino: informa primeiro as garagens (a própria: uso próprio ou alugada/cedida a outro apto; depois
as de outras unidades que utiliza, até responder que não há mais) e então cadastra os veículos, cada um com a sua garagem.
Administração: consulta tudo e corrige o vínculo apartamento-garagem; o condômino vê o aviso do ajuste."""
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
from db import get_db
from garagens import ULTIMA, limpar_placa, normalizar_placa
from mail import ip_de, registrar
from models import AdminUser, GaragemUtilizada, Morador, Unidade, Veiculo
from routers.admin import admin_dep, exigir_proprio
from routers.admin import render as render_admin
from routers.morador import morador_atual, render

router = APIRouter()
SITUACOES = {"propria": "Uso próprio", "alugada": "Alugada/cedida"}
MSG_REPETIDO = "Este registro já existe. Confira a lista."  # envio em dobro barrado pelos índices únicos
MSG_COM_VEICULO = "Há veículo do apartamento cadastrado nesta garagem. Remova-o ou cadastre-o em outra garagem antes."


def agora() -> datetime:
    return datetime.now(timezone.utc)


def numero(texto: str) -> int | None:
    t = texto.strip()
    return int(t) if t.isascii() and t.isdigit() and len(t) <= 6 else None


def quem(m: Morador) -> str:
    return f"{m.nome[:100]} ({m.cpf_fmt})"  # cabe nas colunas de 120


def catalogo(db: Session) -> list[Unidade]:
    """Lista corrida das garagens dos apartamentos, em ordem de bloco e apto."""
    return db.scalars(select(Unidade).where(Unidade.garagem.is_not(None)).order_by(Unidade.bloco, Unidade.apto)).all()


def utilizadas(db: Session, unidade_id) -> list[GaragemUtilizada]:
    return db.scalars(select(GaragemUtilizada).where(GaragemUtilizada.unidade_id == unidade_id, GaragemUtilizada.excluido_em.is_(None))
                      .order_by(GaragemUtilizada.garagem)).all()


def veiculos(db: Session, unidade_id) -> list[Veiculo]:
    return db.scalars(select(Veiculo).where(Veiculo.unidade_id == unidade_id, Veiculo.excluido_em.is_(None)).order_by(Veiculo.criado_em)).all()


def tem_veiculo(db: Session, unidade_id, garagem: int) -> bool:
    return db.scalar(select(exists().where(Veiculo.unidade_id == unidade_id, Veiculo.garagem == garagem, Veiculo.excluido_em.is_(None))))


def voltar(erro: str = "", ok: str = "", para: str = "/morador/garagem") -> RedirectResponse:
    return RedirectResponse(para + (f"?erro={quote(erro)}" if erro else f"?ok={quote(ok)}" if ok else ""), status_code=303)


def incluir_utilizada(db: Session, request: Request, m: Morador, n: int, origem: str) -> str | None:
    """Põe a garagem n entre as que o apartamento utiliza; devolve a mensagem de erro, se houver."""
    u, atuais = m.unidade, utilizadas(db, m.unidade_id)
    if n == u.garagem:
        return "Esta já é a garagem do seu apartamento."
    if not db.scalar(select(Unidade.id).where(Unidade.garagem == n)):
        return "Garagem não encontrada na lista."
    if any(g.garagem == n for g in atuais):
        return f"A garagem {n} já está na sua lista."
    db.add(GaragemUtilizada(unidade_id=u.id, garagem=n, origem=origem, informado_por=quem(m), informado_ip=ip_de(request)))
    return None


# ---------- condômino ----------
@router.get("/morador/garagem")
def pagina(request: Request, erro: str = "", ok: str = "", sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u = m.unidade
    # etapas: 1 situação da garagem própria · 2 outras garagens (até responder que não há mais) · 3 veículos e resumo
    etapa = 0 if not u.garagem else 1 if not u.garagem_uso else 2 if not u.garagens_informadas_em else 3
    lista = catalogo(db)
    return render(request, "morador/garagem.html", morador=m, u=u, etapa=etapa, lista=lista, dono={x.garagem: x for x in lista},
                  utilizadas=utilizadas(db, u.id), veiculos=veiculos(db, u.id), situacoes=SITUACOES, erro=erro, ok=ok)


@router.post("/morador/garagem/situacao")
def situacao(request: Request, uso: str = Form(""), para: str = Form(""), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u, destino = m.unidade, None
    if not u.garagem:
        raise HTTPException(404)
    if uso == "desfazer":  # volta a "não informado": o condômino responde de novo quando quiser
        if tem_veiculo(db, u.id, u.garagem):
            return voltar(MSG_COM_VEICULO)
        u.garagem_uso = u.garagem_uso_para_id = None
        db.commit()
        registrar("Garagem: situação da garagem própria desfeita", request, condomino=m.nome, unidade=u.rotulo, garagem=u.garagem)
        return voltar(ok="Situação da garagem desfeita. Informe de novo quando quiser.")
    if uso not in SITUACOES:
        return voltar("Marque se a garagem é de uso próprio ou se está alugada/cedida.")
    if uso == "alugada":
        try:
            destino = db.get(Unidade, uuid.UUID(para))
        except ValueError:
            destino = None
        if not destino or not destino.garagem:
            return voltar("Para garagem alugada/cedida, informe o bloco e o apto que a está utilizando.")
        if destino.id == u.id:
            return voltar("Quem utiliza a garagem não pode ser o próprio apartamento.")
        if tem_veiculo(db, u.id, u.garagem):
            return voltar(MSG_COM_VEICULO)
    u.garagem_uso, u.garagem_uso_para_id = uso, destino.id if destino else None
    db.commit()
    registrar("Garagem: situação da garagem própria informada", request, condomino=m.nome, unidade=u.rotulo, garagem=u.garagem,
              situacao=SITUACOES[uso], utilizada_por=destino.rotulo if destino else "")
    return voltar(ok="Situação da garagem registrada.")


@router.post("/morador/garagem/utilizadas")
def utilizada_incluir(request: Request, garagem: str = Form(""), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    n = numero(garagem)
    if not m.unidade.garagem_uso:
        return voltar("Informe primeiro a situação da garagem do apartamento.")
    if n is None:
        return voltar("Escolha a garagem na lista.")
    if erro := incluir_utilizada(db, request, m, n, "informada"):
        return voltar(erro)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return voltar(MSG_REPETIDO)
    registrar("Garagem: garagem de outra unidade informada", request, condomino=m.nome, unidade=m.unidade.rotulo, garagem=n)
    return voltar(ok=f"Garagem {n} incluída.")


@router.post("/morador/garagem/concluir")
def concluir(request: Request, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    """Resposta "não utilizo mais nenhuma garagem": libera o cadastro de veículos."""
    u = morador_atual(request, db, sessao).unidade
    if not u.garagem_uso:
        return voltar("Informe primeiro a situação da garagem do apartamento.")
    if not u.garagens_informadas_em:
        u.garagens_informadas_em = agora()
        db.commit()
    return voltar()


@router.post("/morador/garagem/utilizadas/{gid}/excluir")
def utilizada_excluir(request: Request, gid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    g = db.get(GaragemUtilizada, gid)
    if not g or g.unidade_id != m.unidade_id or g.excluido_em:
        raise HTTPException(404)
    if tem_veiculo(db, m.unidade_id, g.garagem):
        return voltar(MSG_COM_VEICULO)
    g.excluido_em, g.excluido_por, g.excluido_ip = agora(), quem(m), ip_de(request)
    db.commit()
    registrar("Garagem: garagem de outra unidade removida da lista", request, condomino=m.nome, unidade=m.unidade.rotulo, garagem=g.garagem)
    return voltar(ok=f"Garagem {g.garagem} removida da sua lista.")


@router.post("/morador/garagem/veiculos")
def veiculo_cadastrar(request: Request, marca: str = Form(""), modelo: str = Form(""), cor: str = Form(""), placa: str = Form(""),
                      garagem: str = Form(""), sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    u = m.unidade
    if not u.garagens_informadas_em:
        return voltar("Informe primeiro as garagens que o apartamento utiliza.")
    marca, modelo, cor = (" ".join(x.split()) for x in (marca, modelo, cor))
    p, n, atuais = normalizar_placa(placa), numero(garagem), veiculos(db, u.id)
    if not (marca and modelo and cor):
        erro = "Informe a marca, o modelo e a cor do veículo."
    elif not p:
        erro = f"Placa inválida (recebido: {limpar_placa(placa)[:10] or 'vazio'}). São 7 caracteres, no formato ABC1234 ou ABC1D23."
    elif any(v.placa == p for v in atuais):
        erro = f"A placa {p} já está cadastrada neste apartamento."
    elif n is None:
        erro = "Indique a garagem em que o veículo fica."
    elif n == u.garagem:
        erro = None if u.garagem_uso == "propria" else ("A garagem do apartamento está marcada como alugada/cedida. "
                                                         "Altere a situação dela ou indique outra garagem.")
    elif any(g.garagem == n for g in utilizadas(db, u.id)):
        erro = None
    else:  # garagem que ainda não estava na lista do apartamento: entra sozinha
        erro = incluir_utilizada(db, request, m, n, "veiculo")
    if erro:
        return voltar(erro)
    db.add(Veiculo(unidade_id=u.id, marca=marca[:40], modelo=modelo[:60], cor=cor[:30], placa=p, garagem=n, cadastrado_por=quem(m), cadastrado_ip=ip_de(request)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return voltar(MSG_REPETIDO)
    registrar("Veículo cadastrado", request, condomino=m.nome, unidade=u.rotulo, veiculo=f"{marca} {modelo} ({cor})", placa=p, garagem=n)
    return voltar(ok=f"Veículo {p} cadastrado na garagem {n}.")


@router.post("/morador/garagem/veiculos/{vid}/excluir")
def veiculo_excluir(request: Request, vid: uuid.UUID, sessao: dict = Depends(auth.exigir("morador")), db: Session = Depends(get_db)):
    m = morador_atual(request, db, sessao)
    v = db.get(Veiculo, vid)
    if not v or v.unidade_id != m.unidade_id or v.excluido_em:
        raise HTTPException(404)
    v.excluido_em, v.excluido_por, v.excluido_ip = agora(), quem(m), ip_de(request)
    db.commit()
    registrar("Veículo removido", request, condomino=m.nome, unidade=m.unidade.rotulo, veiculo=f"{v.marca} {v.modelo} ({v.cor})", placa=v.placa, garagem=v.garagem)
    return voltar(ok=f"Veículo {v.placa} removido.")


# ---------- administração ----------
@router.get("/admin/garagem")
def admin_pagina(request: Request, mostrar: str = "", bloco: str = "", q: str = "", erro: str = "", ok: str = "",
                 admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    lista = catalogo(db)
    usadas, carros = {}, {}
    for g in db.scalars(select(GaragemUtilizada).where(GaragemUtilizada.excluido_em.is_(None))):
        usadas.setdefault(g.unidade_id, []).append(g.garagem)
    for v in db.scalars(select(Veiculo).where(Veiculo.excluido_em.is_(None)).order_by(Veiculo.criado_em)):
        carros.setdefault(v.unidade_id, []).append(v)
    termo = q.strip()
    n, placa = numero(termo), re.sub(r"[^A-Za-z0-9]", "", termo).upper()
    linhas, lista_veiculos = [], []  # linhas: (unidade, garagens que utiliza, quantidade de veículos)
    for u in lista:
        garagens = sorted(([u.garagem] if u.garagem_uso == "propria" else []) + usadas.get(u.id, []))
        veics = carros.get(u.id, [])
        if bloco and u.bloco != bloco:
            continue
        if termo:  # busca por número de garagem (do apto, utilizada ou de um veículo) ou por placa
            achados = [v for v in veics if (v.garagem == n if n is not None else placa in v.placa)]
            if not achados and not (n is not None and (n == u.garagem or n in garagens)):
                continue
        else:
            achados = veics
            if mostrar != "todos" and not (u.garagem_uso or garagens or veics or u.garagem_alterada_em):
                continue  # por padrão, só os apartamentos com algum registro
        linhas.append((u, garagens, len(veics)))
        lista_veiculos += [(u, v) for v in achados]
    return render_admin(request, "admin/garagem.html", linhas=linhas, veiculos=lista_veiculos, lista=lista, dono={x.garagem: x for x in lista},
                        blocos=sorted({x.bloco for x in lista}), situacoes=SITUACOES, ultima=ULTIMA, mostrar=mostrar, bloco=bloco, q=q, erro=erro, ok=ok)


@router.post("/admin/garagem/corrigir")
def admin_corrigir(request: Request, unidade: str = Form(""), garagem: str = Form(""), admin: AdminUser = Depends(admin_dep), db: Session = Depends(get_db)):
    """Corrige a garagem real de um apartamento. Se o número já é de outro apartamento, os dois trocam entre si. Os veículos
    que estavam na garagem própria acompanham o novo número, e os apartamentos afetados passam a ver o aviso do ajuste."""
    exigir_proprio(admin, None)  # conta de teste das lojas só consulta
    try:
        u = db.get(Unidade, uuid.UUID(unidade))
    except ValueError:
        u = None
    n = numero(garagem)
    if not u or not u.garagem or n is None or not 1 <= n <= ULTIMA:
        return voltar(f"Apartamento ou número de garagem inválido (as garagens vão de 1 a {ULTIMA}).", para="/admin/garagem")
    if n == u.garagem:
        return voltar(ok=f"A garagem {n} já é a do {u.rotulo}.", para="/admin/garagem")
    antiga, outro, quando = u.garagem, db.scalar(select(Unidade).where(Unidade.garagem == n)), agora()
    if outro:  # o número é único: libera antes de atribuir
        outro.garagem = None
        db.flush()
    u.garagem = n
    db.flush()
    trocas = [(u, antiga, n)]
    if outro:
        outro.garagem = antiga
        trocas.append((outro, n, antiga))
    for x, de, para in trocas:
        x.garagem_anterior, x.garagem_alterada_em, x.garagem_alterada_por = de, quando, admin.login
        db.execute(update(Veiculo).where(Veiculo.unidade_id == x.id, Veiculo.garagem == de, Veiculo.excluido_em.is_(None)).values(garagem=para))
        for g in db.scalars(select(GaragemUtilizada).where(GaragemUtilizada.unidade_id == x.id, GaragemUtilizada.garagem == para,
                                                           GaragemUtilizada.excluido_em.is_(None))):  # passou a ser a própria: sai das utilizadas
            g.excluido_em, g.excluido_por, g.excluido_ip = quando, f"{admin.login} (correção do vínculo)", ip_de(request)
        if x.garagem_uso == "alugada" and tem_veiculo(db, x.id, para):
            # ficou com veículo na garagem própria, que constava como alugada/cedida: o condômino informa as garagens de novo
            x.garagem_uso = x.garagem_uso_para_id = x.garagens_informadas_em = None
    db.commit()
    registrar("Garagem: vínculo corrigido pela administração", request, admin=admin.login, unidade=u.rotulo, garagem_anterior=antiga, garagem_nova=n,
              troca_com=outro.rotulo if outro else "")
    return voltar(ok=f"{u.rotulo}: garagem {n} (antes: {antiga})." + (f" {outro.rotulo} ficou com a garagem {antiga}." if outro else ""), para="/admin/garagem")
