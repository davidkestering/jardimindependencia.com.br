"""documento opcional (PDF ou Word) anexado ao comunicado

Revision ID: a3e9c7d15f28
Revises: b5c9e1d74fa6
"""
from alembic import op
import sqlalchemy as sa

revision = 'a3e9c7d15f28'
down_revision = 'b5c9e1d74fa6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('comunicado', sa.Column('arquivo', sa.String(length=255), nullable=True))
    op.add_column('comunicado', sa.Column('nome_original', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('comunicado', 'nome_original')
    op.drop_column('comunicado', 'arquivo')
