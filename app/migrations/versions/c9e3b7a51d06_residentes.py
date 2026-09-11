"""residentes do apto e origem do titular

Revision ID: c9e3b7a51d06
Revises: a8d4e6f20c91
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9e3b7a51d06'
down_revision = 'a8d4e6f20c91'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('morador', sa.Column('origem', sa.String(length=16), server_default='site', nullable=False))
    op.create_table('residente',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('nome', sa.String(length=120), nullable=False),
        sa.Column('cpf', sa.String(length=11), nullable=False),
        sa.Column('nascimento', sa.Date(), nullable=False),
        sa.Column('email', sa.String(length=160), nullable=False),
        sa.Column('telefone', sa.String(length=20), nullable=False),
        sa.Column('tipo', sa.String(length=12), nullable=False),
        sa.Column('cadastrado_por', sa.String(length=120), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('unidade_id', 'cpf'))
    op.create_index('ix_residente_unidade_id', 'residente', ['unidade_id'])


def downgrade():
    op.drop_table('residente')
    op.drop_column('morador', 'origem')
