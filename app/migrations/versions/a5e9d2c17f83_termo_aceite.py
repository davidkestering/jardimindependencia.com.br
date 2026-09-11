"""aceite da declaração gravado no morador (texto, data/hora, IP)

Revision ID: a5e9d2c17f83
Revises: f4a8c2e61b95
"""
from alembic import op
import sqlalchemy as sa

revision = 'a5e9d2c17f83'
down_revision = 'f4a8c2e61b95'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('morador', sa.Column('termo_texto', sa.Text(), nullable=True))
    op.add_column('morador', sa.Column('termo_aceito_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('morador', sa.Column('termo_ip', sa.String(length=45), nullable=True))


def downgrade():
    op.drop_column('morador', 'termo_ip')
    op.drop_column('morador', 'termo_aceito_em')
    op.drop_column('morador', 'termo_texto')
