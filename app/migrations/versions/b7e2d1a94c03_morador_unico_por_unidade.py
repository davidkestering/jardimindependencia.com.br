"""morador único por unidade; email/telefone obrigatórios; histórico de decisão

Revision ID: b7e2d1a94c03
Revises: c17c60a286a8
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7e2d1a94c03'
down_revision = 'c17c60a286a8'
branch_labels = None
depends_on = None

OCUPA = sa.text("status IN ('pendente','aprovado','bloqueado')")


def upgrade():
    op.drop_constraint('morador_cpf_key', 'morador', type_='unique')
    op.create_index('ix_morador_cpf', 'morador', ['cpf'])
    op.create_index('uq_morador_unidade_ocupada', 'morador', ['unidade_id'], unique=True, postgresql_where=OCUPA)
    op.alter_column('morador', 'email', nullable=False)
    op.alter_column('morador', 'telefone', nullable=False)
    op.add_column('morador', sa.Column('decidido_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('morador', sa.Column('decidido_por', sa.String(length=60), nullable=True))


def downgrade():
    op.drop_column('morador', 'decidido_por')
    op.drop_column('morador', 'decidido_em')
    op.alter_column('morador', 'telefone', nullable=True)
    op.alter_column('morador', 'email', nullable=True)
    op.drop_index('uq_morador_unidade_ocupada', table_name='morador')
    op.drop_index('ix_morador_cpf', table_name='morador')
    op.create_unique_constraint('morador_cpf_key', 'morador', ['cpf'])
