"""aceite da declaração no cadastro de residentes

Revision ID: d9b4f6a23e17
Revises: c6a2e8d41f70
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9b4f6a23e17'
down_revision = 'c6a2e8d41f70'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('residente', sa.Column('termo_texto', sa.Text(), nullable=True))
    op.add_column('residente', sa.Column('termo_aceito_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('residente', sa.Column('termo_ip', sa.String(length=45), nullable=True))


def downgrade():
    for c in ('termo_ip', 'termo_aceito_em', 'termo_texto'):
        op.drop_column('residente', c)
