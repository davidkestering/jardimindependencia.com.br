"""Modelo de dados. Regra do projeto: toda chave é UUID (gen_random_uuid), nunca incremental."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def fk(table):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False, index=True)


class Unidade(Base):
    __tablename__ = "unidade"
    __table_args__ = (UniqueConstraint("bloco", "apto"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    bloco: Mapped[str] = mapped_column(String(16))   # "01".."27", "PORTARIA", "ADMINISTRACAO"
    apto: Mapped[str] = mapped_column(String(8))     # "001".."404" ou "" para especiais
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    moradores: Mapped[list["Morador"]] = relationship(back_populates="unidade", passive_deletes=True)

    @property
    def rotulo(self):
        return self.bloco if not self.apto else f"Bloco {self.bloco} · Apto {self.apto}"


# Status que "ocupam" o apartamento: enquanto houver um morador nesses status, ninguém mais se cadastra na unidade.
OCUPA_APTO = ("pendente", "aprovado")


class Morador(Base):
    """Uma pessoa por apartamento (índice único parcial). O mesmo CPF pode ter vários apartamentos.
    status: pendente|aprovado (ocupam o apto) · negado (histórico, apto livre)."""
    __tablename__ = "morador"
    __table_args__ = (Index("uq_morador_unidade_ocupada", "unidade_id", unique=True,
                            postgresql_where=text("status IN ('pendente','aprovado')")),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    nome: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(11), index=True)  # só dígitos
    nascimento: Mapped[date] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(160))
    telefone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="pendente", server_default="pendente")
    origem: Mapped[str] = mapped_column(String(16), default="site", server_default="site")  # site|admin|transferencia
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decidido_por: Mapped[str | None] = mapped_column(String(60))  # login do admin que decidiu
    decidido_ip: Mapped[str | None] = mapped_column(String(45))
    termo_texto: Mapped[str | None] = mapped_column(Text)        # declaração exatamente como foi aceita
    termo_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_ip: Mapped[str | None] = mapped_column(String(45))
    comunicados_vistos_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # até quando já viu os comunicados
    unidade: Mapped[Unidade] = relationship(back_populates="moradores")

    @property
    def cpf_fmt(self):
        return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"


# Áreas da administração que podem ser liberadas a um usuário (chave -> rótulo). Prefixo de rota = /admin/<chave>.
AREAS_ADMIN = {"moradores": "Moradores e cadastros", "documentos": "Documentos", "comunicados": "Comunicados",
               "financeiro": "Inadimplência", "assembleias": "Assembleias", "interfone": "Interfone"}


class Residente(Base):
    """Moradores e inquilinos cadastrados pelo titular do apto. Só cadastro: NÃO fazem login (1 CPF por apto = o titular)."""
    __tablename__ = "residente"
    __table_args__ = (UniqueConstraint("unidade_id", "cpf"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    nome: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(11))
    nascimento: Mapped[date] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(160))
    telefone: Mapped[str] = mapped_column(String(20))
    tipo: Mapped[str] = mapped_column(String(12))  # morador|inquilino
    cadastrado_por: Mapped[str] = mapped_column(String(120))
    cadastrado_ip: Mapped[str | None] = mapped_column(String(45))
    termo_texto: Mapped[str | None] = mapped_column(Text)  # declaração aceita pelo titular ao cadastrar este residente
    termo_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    unidade: Mapped[Unidade] = relationship()

    @property
    def cpf_fmt(self):
        return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"


class AdminUser(Base):
    """master: pode tudo e gerencia usuários. Os demais só acessam as áreas listadas em `areas`."""
    __tablename__ = "admin_user"
    id: Mapped[uuid.UUID] = uuid_pk()
    login: Mapped[str] = mapped_column(String(60), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(100))
    nome: Mapped[str] = mapped_column(String(120))
    master: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    areas: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))

    def pode(self, area: str) -> bool:
        return self.master or area in (self.areas or [])


class CategoriaDocumento(Base):
    __tablename__ = "categoria_documento"
    id: Mapped[uuid.UUID] = uuid_pk()
    nome: Mapped[str] = mapped_column(String(80), unique=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Documento(Base):
    __tablename__ = "documento"
    id: Mapped[uuid.UUID] = uuid_pk()
    titulo: Mapped[str] = mapped_column(String(200))
    categoria: Mapped[str] = mapped_column(String(60))
    arquivo: Mapped[str] = mapped_column(String(255))   # caminho relativo em UPLOAD_DIR
    nome_original: Mapped[str] = mapped_column(String(255))
    publico: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    assembleia_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assembleia.id", ondelete="SET NULL"), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enviado_por: Mapped[str | None] = mapped_column(String(60))   # login do admin
    enviado_ip: Mapped[str | None] = mapped_column(String(45))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    assembleia: Mapped["Assembleia | None"] = relationship()


class Cobranca(Base):
    __tablename__ = "cobranca"
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    descricao: Mapped[str] = mapped_column(String(200))
    valor: Mapped[float] = mapped_column(Numeric(12, 2))
    vencimento: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(12), default="aberta", server_default="aberta")  # aberta|paga|cancelada
    pago_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pix_copia_cola: Mapped[str | None] = mapped_column(Text)
    boleto_url: Mapped[str | None] = mapped_column(String(500))
    gateway_ref: Mapped[str | None] = mapped_column(String(120))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unidade: Mapped[Unidade] = relationship()


class Comunicado(Base):
    """visibilidade: rascunho (só admin) | condominos (área logada) | publico (site). publicado_em marca a 1ª publicação."""
    __tablename__ = "comunicado"
    id: Mapped[uuid.UUID] = uuid_pk()
    titulo: Mapped[str] = mapped_column(String(200))
    texto: Mapped[str] = mapped_column(Text)
    visibilidade: Mapped[str] = mapped_column(String(12), default="rascunho", server_default="rascunho")
    autor: Mapped[str] = mapped_column(String(60))                 # login de quem criou
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    publicado_por: Mapped[str | None] = mapped_column(String(60))
    publicado_ip: Mapped[str | None] = mapped_column(String(45))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))


class Inadimplencia(Base):
    """Registro manual de unidade inadimplente. Ativa enquanto encerrado_em for nulo; só uma ativa por unidade."""
    __tablename__ = "inadimplencia"
    __table_args__ = (Index("uq_inadimplencia_ativa", "unidade_id", unique=True, postgresql_where=text("encerrado_em IS NULL")),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    observacao: Mapped[str] = mapped_column(Text)
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    registrado_por: Mapped[str] = mapped_column(String(60))
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encerrado_por: Mapped[str | None] = mapped_column(String(60))
    unidade: Mapped[Unidade] = relationship()


class Assembleia(Base):
    __tablename__ = "assembleia"
    id: Mapped[uuid.UUID] = uuid_pk()
    titulo: Mapped[str] = mapped_column(String(200))
    abre_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    # só pautas não excluídas; as excluídas ficam no banco com seus votos (histórico)
    pautas: Mapped[list["Pauta"]] = relationship(primaryjoin="and_(Pauta.assembleia_id == Assembleia.id, Pauta.excluido_em.is_(None))",
                                                order_by="Pauta.ordem", viewonly=True)


class Pauta(Base):
    __tablename__ = "pauta"
    id: Mapped[uuid.UUID] = uuid_pk()
    assembleia_id: Mapped[uuid.UUID] = fk("assembleia")
    ordem: Mapped[int] = mapped_column(default=1)
    texto: Mapped[str] = mapped_column(Text)
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    assembleia: Mapped[Assembleia] = relationship()
    opcoes: Mapped[list["Opcao"]] = relationship(back_populates="pauta", order_by="Opcao.ordem", cascade="all, delete-orphan", passive_deletes=True)


class Opcao(Base):
    __tablename__ = "opcao"
    id: Mapped[uuid.UUID] = uuid_pk()
    pauta_id: Mapped[uuid.UUID] = fk("pauta")
    ordem: Mapped[int] = mapped_column(default=1)
    texto: Mapped[str] = mapped_column(String(200))
    pauta: Mapped[Pauta] = relationship(back_populates="opcoes")


class Voto(Base):
    __tablename__ = "voto"
    __table_args__ = (UniqueConstraint("unidade_id", "pauta_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    pauta_id: Mapped[uuid.UUID] = fk("pauta")
    opcao_id: Mapped[uuid.UUID] = fk("opcao")
    morador_id: Mapped[uuid.UUID] = fk("morador")
    inadimplente_no_voto: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    votado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    __tablename__ = "push_subscription"
    id: Mapped[uuid.UUID] = uuid_pk()
    morador_id: Mapped[uuid.UUID] = fk("morador")
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys: Mapped[dict] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chamada(Base):
    __tablename__ = "chamada"
    id: Mapped[uuid.UUID] = uuid_pk()
    de_unidade_id: Mapped[uuid.UUID] = fk("unidade")
    para_unidade_id: Mapped[uuid.UUID] = fk("unidade")
    status: Mapped[str] = mapped_column(String(12), default="tocando", server_default="tocando")  # tocando|atendida|recusada|perdida
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Historico(Base):
    """Trilha de auditoria: toda ação relevante (mail.registrar) grava aqui quem (login/CPF), IP, data/hora e detalhes."""
    __tablename__ = "historico"
    id: Mapped[uuid.UUID] = uuid_pk()
    quando: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    tipo: Mapped[str | None] = mapped_column(String(12))      # admin | morador | None (visitante)
    login: Mapped[str | None] = mapped_column(String(160))    # login do admin ou "Nome (CPF)" do condômino
    ip: Mapped[str | None] = mapped_column(String(45))
    acao: Mapped[str] = mapped_column(String(200))
    detalhe: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
