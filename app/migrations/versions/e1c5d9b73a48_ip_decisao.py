"""IP de quem decidiu o acesso e de quem cadastrou residente

Revision ID: e1c5d9b73a48
Revises: d7f1a3c58e29
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1c5d9b73a48'
down_revision = 'd7f1a3c58e29'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('morador', sa.Column('decidido_ip', sa.String(length=45), nullable=True))
    op.add_column('residente', sa.Column('cadastrado_ip', sa.String(length=45), nullable=True))


def downgrade():
    op.drop_column('residente', 'cadastrado_ip')
    op.drop_column('morador', 'decidido_ip')
