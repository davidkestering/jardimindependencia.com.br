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
OCUPA_APTO = ("pendente", "aprovado", "bloqueado")


class Morador(Base):
    """Uma pessoa por apartamento (índice único parcial). O mesmo CPF pode ter vários apartamentos.
    status: pendente|aprovado|bloqueado (ocupam o apto) · negado|revogado (histórico, apto livre)."""
    __tablename__ = "morador"
    __table_args__ = (Index("uq_morador_unidade_ocupada", "unidade_id", unique=True,
                            postgresql_where=text("status IN ('pendente','aprovado','bloqueado')")),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    nome: Mapped[str] = mapped_column(String(120))
    cpf: Mapped[str] = mapped_column(String(11), index=True)  # só dígitos
    nascimento: Mapped[date] = mapped_column(Date)
    email: Mapped[str] = mapped_column(String(160))
    telefone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default="pendente", server_default="pendente")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decidido_por: Mapped[str | None] = mapped_column(String(60))  # login do admin que decidiu
    comunicados_vistos_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # até quando já viu os comunicados
    unidade: Mapped[Unidade] = relationship(back_populates="moradores")

    @property
    def cpf_fmt(self):
        return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"


class AdminUser(Base):
    __tablename__ = "admin_user"
    id: Mapped[uuid.UUID] = uuid_pk()
    login: Mapped[str] = mapped_column(String(60), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(100))
    nome: Mapped[str] = mapped_column(String(120))


class Documento(Base):
    __tablename__ = "documento"
    id: Mapped[uuid.UUID] = uuid_pk()
    titulo: Mapped[str] = mapped_column(String(200))
    categoria: Mapped[str] = mapped_column(String(60))
    arquivo: Mapped[str] = mapped_column(String(255))   # caminho relativo em UPLOAD_DIR
    nome_original: Mapped[str] = mapped_column(String(255))
    publico: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    autor: Mapped[str] = mapped_column(String(60))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publicado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


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
    pautas: Mapped[list["Pauta"]] = relationship(back_populates="assembleia", order_by="Pauta.ordem", cascade="all, delete-orphan", passive_deletes=True)


class Pauta(Base):
    __tablename__ = "pauta"
    id: Mapped[uuid.UUID] = uuid_pk()
    assembleia_id: Mapped[uuid.UUID] = fk("assembleia")
    ordem: Mapped[int] = mapped_column(default=1)
    texto: Mapped[str] = mapped_column(Text)
    assembleia: Mapped[Assembleia] = relationship(back_populates="pautas")
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
