"""histórico de auditoria e exclusão lógica em documentos, comunicados, assembleias, pautas, residentes e usuários

Revision ID: c6a2e8d41f70
Revises: b3d7f1e95c26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c6a2e8d41f70'
down_revision = 'b3d7f1e95c26'
branch_labels = None
depends_on = None
TABELAS = ['documento', 'comunicado', 'assembleia', 'pauta', 'residente', 'admin_user']


def upgrade():
    for t in TABELAS:
        op.add_column(t, sa.Column('excluido_em', sa.DateTime(timezone=True), nullable=True))
        op.add_column(t, sa.Column('excluido_por', sa.String(length=120), nullable=True))
        op.add_column(t, sa.Column('excluido_ip', sa.String(length=45), nullable=True))
    op.create_table('historico',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('quando', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('tipo', sa.String(length=12), nullable=True),
        sa.Column('login', sa.String(length=160), nullable=True),
        sa.Column('ip', sa.String(length=45), nullable=True),
        sa.Column('acao', sa.String(length=200), nullable=False),
        sa.Column('detalhe', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_historico_quando', 'historico', ['quando'])


def downgrade():
    op.drop_table('historico')
    for t in TABELAS:
        for c in ('excluido_ip', 'excluido_por', 'excluido_em'):
            op.drop_column(t, c)
