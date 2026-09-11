"""comunicados da administração

Revision ID: e5b1c8d40a72
Revises: d3a9f7c21e58
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5b1c8d40a72'
down_revision = 'd3a9f7c21e58'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('comunicado',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('visibilidade', sa.String(length=12), server_default='rascunho', nullable=False),
        sa.Column('autor', sa.String(length=60), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('publicado_em', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_comunicado_publicado_em', 'comunicado', ['publicado_em'])
    op.add_column('morador', sa.Column('comunicados_vistos_em', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('morador', 'comunicados_vistos_em')
    op.drop_table('comunicado')
