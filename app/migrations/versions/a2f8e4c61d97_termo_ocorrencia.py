"""aceite da declaração no registro de ocorrência

Revision ID: a2f8e4c61d97
Revises: f1d6b2a84c53
"""
from alembic import op
import sqlalchemy as sa

revision = 'a2f8e4c61d97'
down_revision = 'f1d6b2a84c53'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ocorrencia', sa.Column('termo_texto', sa.Text(), nullable=True))
    op.add_column('ocorrencia', sa.Column('termo_aceito_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('ocorrencia', sa.Column('termo_ip', sa.String(length=45), nullable=True))


def downgrade():
    for c in ('termo_ip', 'termo_aceito_em', 'termo_texto'):
        op.drop_column('ocorrencia', c)
