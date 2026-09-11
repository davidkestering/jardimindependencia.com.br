"""enquetes

Revision ID: e8c1a5d97b34
Revises: d9b4f6a23e17
"""
from alembic import op
import sqlalchemy as sa

revision = 'e8c1a5d97b34'
down_revision = 'd9b4f6a23e17'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('enquete',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('pergunta', sa.String(length=300), nullable=False),
        sa.Column('descricao', sa.Text(), nullable=True),
        sa.Column('abre_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('criado_por', sa.String(length=60), nullable=False),
        sa.Column('criado_ip', sa.String(length=45), nullable=True),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('excluido_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('excluido_por', sa.String(length=120), nullable=True),
        sa.Column('excluido_ip', sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint('id'))
    op.create_table('enquete_opcao',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('enquete_id', sa.UUID(), nullable=False),
        sa.Column('ordem', sa.Integer(), nullable=False),
        sa.Column('texto', sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(['enquete_id'], ['enquete.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_enquete_opcao_enquete_id', 'enquete_opcao', ['enquete_id'])
    op.create_table('enquete_voto',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('enquete_id', sa.UUID(), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('opcao_id', sa.UUID(), nullable=False),
        sa.Column('morador_id', sa.UUID(), nullable=False),
        sa.Column('inadimplente_no_voto', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('votado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['enquete_id'], ['enquete.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['opcao_id'], ['enquete_opcao.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['morador_id'], ['morador.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('enquete_id', 'unidade_id'))
    for c in ('enquete_id', 'unidade_id', 'opcao_id', 'morador_id'):
        op.create_index(f'ix_enquete_voto_{c}', 'enquete_voto', [c])


def downgrade():
    op.drop_table('enquete_voto')
    op.drop_table('enquete_opcao')
    op.drop_table('enquete')
