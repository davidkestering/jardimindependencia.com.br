"""rastreio: quem/IP em documentos e comunicados

Revision ID: b3d7f1e95c26
Revises: a5e9d2c17f83
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3d7f1e95c26'
down_revision = 'a5e9d2c17f83'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('documento', sa.Column('enviado_por', sa.String(length=60), nullable=True))
    op.add_column('documento', sa.Column('enviado_ip', sa.String(length=45), nullable=True))
    op.add_column('comunicado', sa.Column('criado_ip', sa.String(length=45), nullable=True))
    op.add_column('comunicado', sa.Column('publicado_por', sa.String(length=60), nullable=True))
    op.add_column('comunicado', sa.Column('publicado_ip', sa.String(length=45), nullable=True))


def downgrade():
    for t, c in (('comunicado', 'publicado_ip'), ('comunicado', 'publicado_por'), ('comunicado', 'criado_ip'),
                 ('documento', 'enviado_ip'), ('documento', 'enviado_por')):
        op.drop_column(t, c)
