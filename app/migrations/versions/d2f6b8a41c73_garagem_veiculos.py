"""garagem de cada apartamento, garagens utilizadas e veículos; área "garagem" da administração

Revision ID: d2f6b8a41c73
Revises: c4e7a1d93f58
"""
from alembic import op
import sqlalchemy as sa

revision = 'd2f6b8a41c73'
down_revision = 'c4e7a1d93f58'
branch_labels = None
depends_on = None

CONTAS_TESTE = "('usuario.apple', 'usuario.android')"  # revisão das lojas: enxergam todas as áreas


def _auditoria():
    return [sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('excluido_em', sa.DateTime(timezone=True), nullable=True),
            sa.Column('excluido_por', sa.String(length=120), nullable=True),
            sa.Column('excluido_ip', sa.String(length=45), nullable=True)]


def upgrade():
    # os números das garagens são preenchidos pelo seed() do app a partir de garagens.py (vale também para instalação nova)
    op.add_column('unidade', sa.Column('garagem', sa.Integer(), nullable=True))
    op.add_column('unidade', sa.Column('garagem_convencao', sa.Integer(), nullable=True))
    op.add_column('unidade', sa.Column('garagem_uso', sa.String(length=10), nullable=True))
    op.add_column('unidade', sa.Column('garagem_uso_para_id', sa.UUID(), nullable=True))
    op.add_column('unidade', sa.Column('garagens_informadas_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('unidade', sa.Column('garagem_anterior', sa.Integer(), nullable=True))
    op.add_column('unidade', sa.Column('garagem_alterada_em', sa.DateTime(timezone=True), nullable=True))
    op.add_column('unidade', sa.Column('garagem_alterada_por', sa.String(length=60), nullable=True))
    op.create_unique_constraint('unidade_garagem_key', 'unidade', ['garagem'])
    op.create_foreign_key('unidade_garagem_uso_para_id_fkey', 'unidade', 'unidade', ['garagem_uso_para_id'], ['id'], ondelete='SET NULL')
    op.create_table('garagem_utilizada',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('garagem', sa.Integer(), nullable=False),
        sa.Column('origem', sa.String(length=10), server_default='informada', nullable=False),
        sa.Column('informado_por', sa.String(length=120), nullable=False),
        sa.Column('informado_ip', sa.String(length=45), nullable=True),
        *_auditoria(),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_garagem_utilizada_unidade_id', 'garagem_utilizada', ['unidade_id'])
    op.execute("CREATE UNIQUE INDEX uq_garagem_utilizada_ativa ON garagem_utilizada (unidade_id, garagem) WHERE excluido_em IS NULL")
    op.create_table('veiculo',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('marca', sa.String(length=40), nullable=False),
        sa.Column('modelo', sa.String(length=60), nullable=False),
        sa.Column('cor', sa.String(length=30), nullable=False),
        sa.Column('placa', sa.String(length=7), nullable=False),
        sa.Column('garagem', sa.Integer(), nullable=False),
        sa.Column('cadastrado_por', sa.String(length=120), nullable=False),
        sa.Column('cadastrado_ip', sa.String(length=45), nullable=True),
        *_auditoria(),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_veiculo_unidade_id', 'veiculo', ['unidade_id'])
    op.execute("CREATE UNIQUE INDEX uq_veiculo_placa_ativa ON veiculo (unidade_id, placa) WHERE excluido_em IS NULL")
    op.execute(f"""UPDATE admin_user SET areas = areas || '["garagem"]'::jsonb WHERE login IN {CONTAS_TESTE} AND NOT areas @> '["garagem"]'::jsonb""")


def downgrade():
    op.execute(f"""UPDATE admin_user SET areas = areas - 'garagem' WHERE login IN {CONTAS_TESTE}""")
    op.drop_table('veiculo')
    op.drop_table('garagem_utilizada')
    op.drop_constraint('unidade_garagem_uso_para_id_fkey', 'unidade', type_='foreignkey')
    op.drop_constraint('unidade_garagem_key', 'unidade', type_='unique')
    for c in ('garagem_alterada_por', 'garagem_alterada_em', 'garagem_anterior', 'garagens_informadas_em', 'garagem_uso_para_id', 'garagem_uso',
              'garagem_convencao', 'garagem'):
        op.drop_column('unidade', c)
