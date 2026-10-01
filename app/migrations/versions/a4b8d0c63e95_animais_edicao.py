"""animais: quem alterou o cadastro; unidade que informa não possuir animais de estimação

Revision ID: a4b8d0c63e95
Revises: e3a7c9b52d84
"""
from alembic import op
import sqlalchemy as sa

revision = 'a4b8d0c63e95'
down_revision = 'e3a7c9b52d84'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('animal', sa.Column('alterado_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('animal', sa.Column('alterado_por', sa.String(length=120), nullable=True))
    op.add_column('animal', sa.Column('alterado_ip', sa.String(length=45), nullable=True))
    op.add_column('unidade', sa.Column('sem_animais_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('unidade', sa.Column('sem_animais_por', sa.String(length=120), nullable=True))


def downgrade():
    for tabela, coluna in (('unidade', 'sem_animais_por'), ('unidade', 'sem_animais_em'), ('animal', 'alterado_ip'), ('animal', 'alterado_por'),
                           ('animal', 'alterado_em')):
        op.drop_column(tabela, coluna)
