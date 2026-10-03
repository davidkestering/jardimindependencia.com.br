"""Modelo de dados. Regra do projeto: toda chave é UUID (gen_random_uuid), nunca incremental."""
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, Sequence, String, Text, UniqueConstraint, func, or_, text, true
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# Unidades só dos testes automatizados (scripts/check_*.py): Bloco 99, aptos 999 (a principal) a 996, criadas inativas pelo
# seed do app. Para o site elas não existem: ficam fora de toda lista, seleção, contagem e busca por bloco e apto. Só o
# processo dos próprios testes as enxerga, porque scripts/unidades_teste.py liga EM_TESTE nele; o servidor nunca liga.
BLOCO_TESTE = "99"
APTOS_TESTE = ("999", "998", "997", "996")
GARAGEM_TESTE = 9000  # garagem da unidade de teste = 9000 + nº do apto: fora da numeração real (garagens.ULTIMA)
EM_TESTE = False


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
    # Garagem (garagens.py): vaga real do apto (única; a administração pode corrigir) e a que consta na convenção registrada.
    garagem: Mapped[int | None] = mapped_column(Integer, unique=True)
    garagem_convencao: Mapped[int | None] = mapped_column(Integer)
    # Declarado pelo condômino: propria | alugada (alugada/cedida, com o apto que a utiliza); nulo = ainda não informou.
    garagem_uso: Mapped[str | None] = mapped_column(String(10))
    garagem_uso_para_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("unidade.id", ondelete="SET NULL"))
    garagens_informadas_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # respondeu que não utiliza mais nenhuma garagem
    # Último ajuste do vínculo pela administração: vira aviso na área do condômino.
    garagem_anterior: Mapped[int | None] = mapped_column(Integer)
    garagem_alterada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    garagem_alterada_por: Mapped[str | None] = mapped_column(String(60))
    # Animais de estimação: o condômino informou que a unidade não possui (cadastrar um animal apaga a declaração).
    sem_animais_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sem_animais_por: Mapped[str | None] = mapped_column(String(120))
    moradores: Mapped[list["Morador"]] = relationship(back_populates="unidade", passive_deletes=True)
    garagem_uso_para: Mapped["Unidade | None"] = relationship(remote_side="Unidade.id", foreign_keys="Unidade.garagem_uso_para_id")

    @property
    def rotulo(self):
        return self.bloco if not self.apto else f"Bloco {self.bloco} · Apto {self.apto}"

    @property
    def rotulo_garagem(self):
        return f"{self.rotulo} · Garagem {self.garagem}" if self.garagem else self.rotulo

    @property
    def teste(self) -> bool:
        return self.bloco == BLOCO_TESTE

    @hybrid_property
    def em_uso(self) -> bool:
        """Unidade que o site considera: a ativa. Use sempre no lugar de `ativa` (as de teste só contam dentro dos testes)."""
        return self.ativa or (EM_TESTE and self.teste)

    @em_uso.expression
    def em_uso(cls):
        return or_(cls.ativa, cls.bloco == BLOCO_TESTE) if EM_TESTE else cls.ativa

    @hybrid_property
    def visivel(self) -> bool:
        """Filtro das listas de unidades que não olham `ativa`: deixa de fora as unidades de teste (menos dentro dos testes)."""
        return EM_TESTE or not self.teste

    @visivel.expression
    def visivel(cls):
        return true() if EM_TESTE else cls.bloco != BLOCO_TESTE


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
    transferido_motivo: Mapped[str | None] = mapped_column(String(500))  # justificativa obrigatória ao transferir o acesso
    termo_texto: Mapped[str | None] = mapped_column(Text)        # declaração exatamente como foi aceita
    termo_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_ip: Mapped[str | None] = mapped_column(String(45))
    comunicados_vistos_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # até quando já viu os comunicados
    unidade: Mapped[Unidade] = relationship(back_populates="moradores")

    @property
    def cpf_fmt(self):
        return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"


# Áreas da administração que podem ser liberadas a um usuário (chave -> rótulo). Prefixo de rota = /admin/<chave>.
AREAS_ADMIN = {"moradores": "Moradores e cadastros", "documentos": "Documentos", "comunicados": "Comunicados", "fale-conosco": "Fale Conosco",
               "financeiro": "Inadimplência", "assembleias": "Assembleias", "enquetes": "Enquetes", "ocorrencias": "Ocorrências", "interfone": "Interfone",
               "garagem": "Garagem e Veículos", "animais": "Animais de Estimação"}


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
    excluido_motivo: Mapped[str | None] = mapped_column(String(500))  # justificativa obrigatória na remoção pelo condômino
    unidade: Mapped[Unidade] = relationship()

    @property
    def cpf_fmt(self):
        return f"{self.cpf[:3]}.{self.cpf[3:6]}.{self.cpf[6:9]}-{self.cpf[9:]}"


# Usuários de demonstração (revisão da App Store e da Google Play): navegam em tudo, mas só alteram ou excluem o que eles mesmos criaram.
LOGIN_TESTE = "usuario.apple"
LOGINS_TESTE = (LOGIN_TESTE, "usuario.android")
# Conta da portaria: usuário da administração só com a área Interfone; no interfone fala como a unidade PORTARIA.
LOGIN_PORTARIA = "usuario.portaria"


class AdminUser(Base):
    """master: pode tudo e gerencia usuários. Os demais só acessam as áreas listadas em `areas`.
    teste (login em LOGINS_TESTE): não altera nem exclui registro alheio e não notifica os condôminos.
    portaria (login LOGIN_PORTARIA): no interfone é a unidade PORTARIA; os demais usuários são a ADMINISTRACAO."""
    __tablename__ = "admin_user"
    id: Mapped[uuid.UUID] = uuid_pk()
    login: Mapped[str] = mapped_column(String(60), unique=True)
    senha_hash: Mapped[str] = mapped_column(String(100))
    nome: Mapped[str] = mapped_column(String(120))
    master: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    areas: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    criado_por: Mapped[str | None] = mapped_column(String(60))
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    alterado_por: Mapped[str | None] = mapped_column(String(60))   # última alteração de áreas ou senha
    alterado_ip: Mapped[str | None] = mapped_column(String(45))
    alterado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))

    def pode(self, area: str) -> bool:
        return self.master or area in (self.areas or [])

    @property
    def teste(self) -> bool:
        return self.login in LOGINS_TESTE

    @property
    def portaria(self) -> bool:
        return self.login == LOGIN_PORTARIA


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
    competencia: Mapped[date] = mapped_column(Date, index=True)  # data de assinatura/referência do documento (não a do envio)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    enviado_por: Mapped[str | None] = mapped_column(String(60))   # login do admin
    enviado_ip: Mapped[str | None] = mapped_column(String(45))
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    excluido_motivo: Mapped[str | None] = mapped_column(String(500))  # justificativa obrigatória na exclusão
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
    registrado_ip: Mapped[str | None] = mapped_column(String(45))
    encerrado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encerrado_por: Mapped[str | None] = mapped_column(String(60))
    encerrado_ip: Mapped[str | None] = mapped_column(String(45))
    unidade: Mapped[Unidade] = relationship()


class Assembleia(Base):
    __tablename__ = "assembleia"
    id: Mapped[uuid.UUID] = uuid_pk()
    titulo: Mapped[str] = mapped_column(String(200))
    abre_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criado_por: Mapped[str | None] = mapped_column(String(60))
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
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
    criado_por: Mapped[str | None] = mapped_column(String(60))
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
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


class GaragemUtilizada(Base):
    """Garagem de outro apartamento que a unidade informa utilizar (alugada ou cedida). origem: 'informada' na pergunta das
    garagens, ou 'veiculo' quando entrou sozinha porque um veículo foi cadastrado nela."""
    __tablename__ = "garagem_utilizada"
    __table_args__ = (Index("uq_garagem_utilizada_ativa", "unidade_id", "garagem", unique=True, postgresql_where=text("excluido_em IS NULL")),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    garagem: Mapped[int] = mapped_column(Integer)
    origem: Mapped[str] = mapped_column(String(10), default="informada", server_default="informada")
    informado_por: Mapped[str] = mapped_column(String(120))
    informado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))


class Veiculo(Base):
    """Veículo cadastrado pelo condômino. A garagem (número) é obrigatória; mais de um veículo pode ficar na mesma."""
    __tablename__ = "veiculo"
    __table_args__ = (Index("uq_veiculo_placa_ativa", "unidade_id", "placa", unique=True, postgresql_where=text("excluido_em IS NULL")),)
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    marca: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(60))
    cor: Mapped[str] = mapped_column(String(30))
    placa: Mapped[str] = mapped_column(String(7))  # sem traço, maiúsculas (garagens.normalizar_placa)
    garagem: Mapped[int] = mapped_column(Integer)
    cadastrado_por: Mapped[str] = mapped_column(String(120))
    cadastrado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    unidade: Mapped[Unidade] = relationship()


class Animal(Base):
    """Animal de estimação cadastrado pelo condômino. numero: sequencial do animal dentro do apartamento, nunca reaproveitado
    (dá nome à foto: imagens_animais/BL_XX_AP_XXX_animal_<numero>.<extensão>)."""
    __tablename__ = "animal"
    __table_args__ = (UniqueConstraint("unidade_id", "numero"),
                      Index("uq_animal_ativo", "unidade_id", text("lower(nome)"), "tipo", unique=True, postgresql_where=text("excluido_em IS NULL")))
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    numero: Mapped[int] = mapped_column(Integer)
    nome: Mapped[str] = mapped_column(String(60))
    tipo: Mapped[str] = mapped_column(String(20))  # um dos routers.animais.TIPOS
    raca: Mapped[str | None] = mapped_column(String(60))
    foto: Mapped[str | None] = mapped_column(String(255))  # caminho relativo em UPLOAD_DIR
    cadastrado_por: Mapped[str] = mapped_column(String(120))
    cadastrado_ip: Mapped[str | None] = mapped_column(String(45))
    alterado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # última edição pelo condômino
    alterado_por: Mapped[str | None] = mapped_column(String(120))
    alterado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # exclusão lógica: nunca apagar de verdade
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))


class PushSubscription(Base):
    __tablename__ = "push_subscription"
    id: Mapped[uuid.UUID] = uuid_pk()
    morador_id: Mapped[uuid.UUID] = fk("morador")
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    keys: Mapped[dict] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DispositivoApp(Base):
    """Token de push dos apps: APNs (`plataforma` ios) ou FCM (android). Um token por aparelho; se o aparelho troca de dono,
    o token migra para o morador atual. `ambiente` decide o host da APNs (production = App Store/TestFlight, sandbox = build
    do Xcode); no Android é sempre production. Token único pelo md5: o do FCM não tem tamanho fixo e o btree não indexa texto longo."""
    __tablename__ = "dispositivo_app"
    __table_args__ = (Index("uq_dispositivo_app_token", text("md5(token)"), unique=True),)
    id: Mapped[uuid.UUID] = uuid_pk()
    morador_id: Mapped[uuid.UUID] = fk("morador")
    token: Mapped[str] = mapped_column(Text)
    plataforma: Mapped[str] = mapped_column(String(10), default="ios", server_default="ios")
    ambiente: Mapped[str] = mapped_column(String(12), default="production", server_default="production")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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


class Enquete(Base):
    """Enquete da administração: uma pergunta, opções, período de votação, 1 voto por apto (regra de inadimplência igual à assembleia)."""
    __tablename__ = "enquete"
    id: Mapped[uuid.UUID] = uuid_pk()
    pergunta: Mapped[str] = mapped_column(String(300))
    descricao: Mapped[str | None] = mapped_column(Text)
    abre_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    criado_por: Mapped[str] = mapped_column(String(60))
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    excluido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excluido_por: Mapped[str | None] = mapped_column(String(120))
    excluido_ip: Mapped[str | None] = mapped_column(String(45))
    opcoes: Mapped[list["EnqueteOpcao"]] = relationship(order_by="EnqueteOpcao.ordem", viewonly=True)


class EnqueteOpcao(Base):
    __tablename__ = "enquete_opcao"
    id: Mapped[uuid.UUID] = uuid_pk()
    enquete_id: Mapped[uuid.UUID] = fk("enquete")
    ordem: Mapped[int] = mapped_column(default=1)
    texto: Mapped[str] = mapped_column(String(200))


class EnqueteVoto(Base):
    __tablename__ = "enquete_voto"
    __table_args__ = (UniqueConstraint("enquete_id", "unidade_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    enquete_id: Mapped[uuid.UUID] = fk("enquete")
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    opcao_id: Mapped[uuid.UUID] = fk("enquete_opcao")
    morador_id: Mapped[uuid.UUID] = fk("morador")
    inadimplente_no_voto: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    votado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ocorrencia(Base):
    """Registro de ocorrência do condômino. Imutável: sem edição nem exclusão. Conversa por mensagens até o condômino finalizar."""
    __tablename__ = "ocorrencia"
    id: Mapped[uuid.UUID] = uuid_pk()
    numero: Mapped[int] = mapped_column(Integer, Sequence("ocorrencia_numero_seq"), server_default=Sequence("ocorrencia_numero_seq").next_value(), unique=True)
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    morador_id: Mapped[uuid.UUID] = fk("morador")
    titulo: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="aberta", server_default="aberta")  # aberta|finalizada
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    finalizada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalizada_ip: Mapped[str | None] = mapped_column(String(45))
    ultima_resposta_admin_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vista_pelo_morador_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_msg_morador_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vista_pela_admin_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_texto: Mapped[str | None] = mapped_column(Text)  # declaração aceita ao registrar
    termo_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_ip: Mapped[str | None] = mapped_column(String(45))
    unidade: Mapped[Unidade] = relationship()
    morador: Mapped[Morador] = relationship()
    mensagens: Mapped[list["OcorrenciaMensagem"]] = relationship(order_by="OcorrenciaMensagem.criado_em", viewonly=True)

    @property
    def tem_resposta_nova(self):
        return bool(self.ultima_resposta_admin_em and (not self.vista_pelo_morador_em or self.ultima_resposta_admin_em > self.vista_pelo_morador_em))

    @property
    def aguarda_admin(self):
        return bool(self.ultima_msg_morador_em and (not self.vista_pela_admin_em or self.ultima_msg_morador_em > self.vista_pela_admin_em))


class OcorrenciaMensagem(Base):
    __tablename__ = "ocorrencia_mensagem"
    id: Mapped[uuid.UUID] = uuid_pk()
    ocorrencia_id: Mapped[uuid.UUID] = fk("ocorrencia")
    autor_tipo: Mapped[str] = mapped_column(String(12))   # morador|admin
    autor: Mapped[str] = mapped_column(String(160))       # nome do condômino ou login do admin
    texto: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ip: Mapped[str | None] = mapped_column(String(45))
    anexos: Mapped[list["OcorrenciaAnexo"]] = relationship(order_by="OcorrenciaAnexo.criado_em", viewonly=True)


class OcorrenciaAnexo(Base):
    __tablename__ = "ocorrencia_anexo"
    id: Mapped[uuid.UUID] = uuid_pk()
    mensagem_id: Mapped[uuid.UUID] = fk("ocorrencia_mensagem")
    arquivo: Mapped[str] = mapped_column(String(255))       # caminho relativo em UPLOAD_DIR
    nome_original: Mapped[str] = mapped_column(String(255))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FaleConosco(Base):
    """Mensagem do condômino à administração: sugestão, reclamação, ideia, conselho ou elogio. NÃO é ocorrência:
    não tem número, resposta nem finalização. Imutável: sem edição nem exclusão; a administração só lê."""
    __tablename__ = "fale_conosco"
    id: Mapped[uuid.UUID] = uuid_pk()
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    morador_id: Mapped[uuid.UUID] = fk("morador")
    tipo: Mapped[str] = mapped_column(String(12))  # um dos routers.fale_conosco.TIPOS
    texto: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    criado_ip: Mapped[str | None] = mapped_column(String(45))
    termo_texto: Mapped[str | None] = mapped_column(Text)  # declaração aceita ao enviar
    termo_aceito_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termo_ip: Mapped[str | None] = mapped_column(String(45))
    lida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # primeira vez que a administração abriu
    lida_por: Mapped[str | None] = mapped_column(String(60))
    unidade: Mapped[Unidade] = relationship()
    morador: Mapped[Morador] = relationship()
    anexos: Mapped[list["FaleConoscoAnexo"]] = relationship(order_by="FaleConoscoAnexo.numero", viewonly=True)


class FaleConoscoAnexo(Base):
    """Imagem ou vídeo de uma mensagem do Fale Conosco. numero: sequencial do arquivo dentro do apartamento, nunca reaproveitado
    (dá nome ao arquivo: arquivos_fale_conosco/BL_XX_AP_XXX_fale_DDMMYYYY_HHMMSS_<numero>.<extensão>)."""
    __tablename__ = "fale_conosco_anexo"
    __table_args__ = (UniqueConstraint("unidade_id", "numero"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    mensagem_id: Mapped[uuid.UUID] = fk("fale_conosco")
    unidade_id: Mapped[uuid.UUID] = fk("unidade")
    numero: Mapped[int] = mapped_column(Integer)
    arquivo: Mapped[str] = mapped_column(String(255))       # caminho relativo em UPLOAD_DIR
    nome_original: Mapped[str] = mapped_column(String(255))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
