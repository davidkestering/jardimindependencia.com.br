"""o documento do comunicado passa a ser um Documento (categoria Comunicados): troca arquivo/nome_original por documento_id

Revision ID: c7d2e8f41a96
Revises: a3e9c7d15f28
"""
from alembic import op
import sqlalchemy as sa

revision = 'c7d2e8f41a96'
down_revision = 'a3e9c7d15f28'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('comunicado', 'nome_original')
    op.drop_column('comunicado', 'arquivo')
    op.add_column('comunicado', sa.Column('documento_id', sa.UUID(), sa.ForeignKey('documento.id', ondelete='SET NULL'), nullable=True))
    op.create_index('ix_comunicado_documento_id', 'comunicado', ['documento_id'])
    op.execute("insert into categoria_documento (nome) values ('Comunicados') on conflict (nome) do nothing")


def downgrade():
    op.drop_index('ix_comunicado_documento_id', table_name='comunicado')
    op.drop_column('comunicado', 'documento_id')
    op.add_column('comunicado', sa.Column('arquivo', sa.String(length=255), nullable=True))
    op.add_column('comunicado', sa.Column('nome_original', sa.String(length=255), nullable=True))
