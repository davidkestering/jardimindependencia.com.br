"""IP de quem registrou e encerrou a inadimplência

Revision ID: b8e3d1f76a29
Revises: a2f8e4c61d97
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8e3d1f76a29'
down_revision = 'a2f8e4c61d97'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('inadimplencia', sa.Column('registrado_ip', sa.String(length=45), nullable=True))
    op.add_column('inadimplencia', sa.Column('encerrado_ip', sa.String(length=45), nullable=True))


def downgrade():
    op.drop_column('inadimplencia', 'encerrado_ip')
    op.drop_column('inadimplencia', 'registrado_ip')
