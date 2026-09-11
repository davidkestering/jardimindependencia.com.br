"""ocorrências do condômino (imutáveis) com mensagens e anexos

Revision ID: f1d6b2a84c53
Revises: e8c1a5d97b34
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1d6b2a84c53'
down_revision = 'e8c1a5d97b34'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SEQUENCE IF NOT EXISTS ocorrencia_numero_seq")
    op.create_table('ocorrencia',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('numero', sa.Integer(), server_default=sa.text("nextval('ocorrencia_numero_seq')"), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('morador_id', sa.UUID(), nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='aberta', nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('criado_ip', sa.String(length=45), nullable=True),
        sa.Column('finalizada_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finalizada_ip', sa.String(length=45), nullable=True),
        sa.Column('ultima_resposta_admin_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('vista_pelo_morador_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ultima_msg_morador_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('vista_pela_admin_em', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['morador_id'], ['morador.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('numero'))
    op.create_index('ix_ocorrencia_unidade_id', 'ocorrencia', ['unidade_id'])
    op.create_index('ix_ocorrencia_morador_id', 'ocorrencia', ['morador_id'])
    op.create_index('ix_ocorrencia_criado_em', 'ocorrencia', ['criado_em'])
    op.create_table('ocorrencia_mensagem',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('ocorrencia_id', sa.UUID(), nullable=False),
        sa.Column('autor_tipo', sa.String(length=12), nullable=False),
        sa.Column('autor', sa.String(length=160), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(['ocorrencia_id'], ['ocorrencia.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_ocorrencia_mensagem_ocorrencia_id', 'ocorrencia_mensagem', ['ocorrencia_id'])
    op.create_table('ocorrencia_anexo',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('mensagem_id', sa.UUID(), nullable=False),
        sa.Column('arquivo', sa.String(length=255), nullable=False),
        sa.Column('nome_original', sa.String(length=255), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mensagem_id'], ['ocorrencia_mensagem.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_ocorrencia_anexo_mensagem_id', 'ocorrencia_anexo', ['mensagem_id'])


def downgrade():
    op.drop_table('ocorrencia_anexo')
    op.drop_table('ocorrencia_mensagem')
    op.drop_table('ocorrencia')
    op.execute("DROP SEQUENCE IF EXISTS ocorrencia_numero_seq")
