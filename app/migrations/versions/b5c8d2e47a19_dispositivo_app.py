"""tokens de push do app iOS (APNs)

Revision ID: b5c8d2e47a19
Revises: a3c9e17f5b62
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'b5c8d2e47a19'
down_revision = 'a3c9e17f5b62'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('dispositivo_app',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), primary_key=True),
        sa.Column('morador_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('morador.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('token', sa.String(length=200), nullable=False, unique=True),
        sa.Column('plataforma', sa.String(length=10), server_default='ios', nullable=False),
        sa.Column('ambiente', sa.String(length=12), server_default='production', nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table('dispositivo_app')
