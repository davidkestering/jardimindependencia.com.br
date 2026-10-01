"""animais de estimação de cada apartamento; área "animais" da administração

Revision ID: e3a7c9b52d84
Revises: d2f6b8a41c73
"""
from alembic import op
import sqlalchemy as sa

revision = 'e3a7c9b52d84'
down_revision = 'd2f6b8a41c73'
branch_labels = None
depends_on = None

CONTAS_TESTE = "('usuario.apple', 'usuario.android')"  # revisão das lojas: enxergam todas as áreas


def upgrade():
    op.create_table('animal',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=60), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False),
        sa.Column('raca', sa.String(length=60), nullable=True),
        sa.Column('foto', sa.String(length=255), nullable=True),
        sa.Column('cadastrado_por', sa.String(length=120), nullable=False),
        sa.Column('cadastrado_ip', sa.String(length=45), nullable=True),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('excluido_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('excluido_por', sa.String(length=120), nullable=True),
        sa.Column('excluido_ip', sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('unidade_id', 'numero'))
    op.create_index('ix_animal_unidade_id', 'animal', ['unidade_id'])
    op.execute("CREATE UNIQUE INDEX uq_animal_ativo ON animal (unidade_id, lower(nome), tipo) WHERE excluido_em IS NULL")
    op.execute(f"""UPDATE admin_user SET areas = areas || '["animais"]'::jsonb WHERE login IN {CONTAS_TESTE} AND NOT areas @> '["animais"]'::jsonb""")


def downgrade():
    op.execute(f"""UPDATE admin_user SET areas = areas - 'animais' WHERE login IN {CONTAS_TESTE}""")
    op.drop_table('animal')
