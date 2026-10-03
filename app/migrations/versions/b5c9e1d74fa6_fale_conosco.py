"""fale conosco: mensagens do condômino à administração (não são ocorrências) e anexos; área "fale-conosco" da administração

Revision ID: b5c9e1d74fa6
Revises: a4b8d0c63e95
"""
from alembic import op
import sqlalchemy as sa

revision = 'b5c9e1d74fa6'
down_revision = 'a4b8d0c63e95'
branch_labels = None
depends_on = None

CONTAS_TESTE = "('usuario.apple', 'usuario.android')"  # revisão das lojas: enxergam todas as áreas


def upgrade():
    op.create_table('fale_conosco',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('morador_id', sa.UUID(), nullable=False),
        sa.Column('tipo', sa.String(length=12), nullable=False),
        sa.Column('texto', sa.Text(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('criado_ip', sa.String(length=45), nullable=True),
        sa.Column('termo_texto', sa.Text(), nullable=True),
        sa.Column('termo_aceito_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('termo_ip', sa.String(length=45), nullable=True),
        sa.Column('lida_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lida_por', sa.String(length=60), nullable=True),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['morador_id'], ['morador.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'))
    for coluna in ('unidade_id', 'morador_id', 'criado_em'):
        op.create_index(f'ix_fale_conosco_{coluna}', 'fale_conosco', [coluna])
    op.create_table('fale_conosco_anexo',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('mensagem_id', sa.UUID(), nullable=False),
        sa.Column('unidade_id', sa.UUID(), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=False),
        sa.Column('arquivo', sa.String(length=255), nullable=False),
        sa.Column('nome_original', sa.String(length=255), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['mensagem_id'], ['fale_conosco.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidade.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('unidade_id', 'numero'))
    for coluna in ('mensagem_id', 'unidade_id'):
        op.create_index(f'ix_fale_conosco_anexo_{coluna}', 'fale_conosco_anexo', [coluna])
    op.execute(f"""UPDATE admin_user SET areas = areas || '["fale-conosco"]'::jsonb WHERE login IN {CONTAS_TESTE} AND NOT areas @> '["fale-conosco"]'::jsonb""")


def downgrade():
    op.execute(f"""UPDATE admin_user SET areas = areas - 'fale-conosco' WHERE login IN {CONTAS_TESTE}""")
    op.drop_table('fale_conosco_anexo')
    op.drop_table('fale_conosco')
