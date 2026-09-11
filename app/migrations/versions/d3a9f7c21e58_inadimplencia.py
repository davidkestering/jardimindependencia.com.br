"""registro manual de inadimplência

Revision ID: d3a9f7c21e58
Revises: b7e2d1a94c03
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3a9f7c21e58'
down_revision = 'b7e2d1a94c03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('inadimplencia',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('observacao', sa.Text(), nullable=False),
        sa.Column('registrado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('registrado_por', sa.String(length=60), nullable=False),
        sa.Column('encerrado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('encerrado_por', sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_inadimplencia_unidade_id', 'inadimplencia', ['unidade_id'])
    op.create_index('uq_inadimplencia_ativa', 'inadimplencia', ['unidade_id'], unique=True, postgresql_where=sa.text('encerrado_em IS NULL'))


def downgrade():
    op.drop_table('inadimplencia')
