"""morador: só pendente/aprovado/negado

Revision ID: a8d4e6f20c91
Revises: f2c7a9e13b64
"""
from alembic import op
import sqlalchemy as sa

revision = 'a8d4e6f20c91'
down_revision = 'f2c7a9e13b64'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE morador SET status = 'negado' WHERE status IN ('bloqueado', 'revogado')")
    op.drop_index('uq_morador_unidade_ocupada', table_name='morador')
    op.create_index('uq_morador_unidade_ocupada', 'morador', ['unidade_id'], unique=True, postgresql_where=sa.text("status IN ('pendente','aprovado')"))


def downgrade():
    op.drop_index('uq_morador_unidade_ocupada', table_name='morador')
    op.create_index('uq_morador_unidade_ocupada', 'morador', ['unidade_id'], unique=True, postgresql_where=sa.text("status IN ('pendente','aprovado','bloqueado')"))
