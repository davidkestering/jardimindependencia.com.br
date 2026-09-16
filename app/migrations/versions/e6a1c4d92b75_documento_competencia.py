"""competência do documento (data de assinatura/referência); documentos já enviados recebem a data do envio

Revision ID: e6a1c4d92b75
Revises: d4b7c9e15f60
"""
from alembic import op
import sqlalchemy as sa

revision = 'e6a1c4d92b75'
down_revision = 'd4b7c9e15f60'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('documento', sa.Column('competencia', sa.Date(), nullable=True))
    op.execute("update documento set competencia = (criado_em at time zone 'America/Belem')::date")
    op.alter_column('documento', 'competencia', nullable=False)
    op.create_index('ix_documento_competencia', 'documento', ['competencia'])


def downgrade():
    op.drop_index('ix_documento_competencia', table_name='documento')
    op.drop_column('documento', 'competencia')
