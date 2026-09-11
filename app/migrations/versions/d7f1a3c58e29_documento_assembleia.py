"""documento vinculado a assembleia

Revision ID: d7f1a3c58e29
Revises: c9e3b7a51d06
"""
from alembic import op
import sqlalchemy as sa

revision = 'd7f1a3c58e29'
down_revision = 'c9e3b7a51d06'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('documento', sa.Column('assembleia_id', sa.UUID(), nullable=True))
    op.create_foreign_key('documento_assembleia_id_fkey', 'documento', 'assembleia', ['assembleia_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_documento_assembleia_id', 'documento', ['assembleia_id'])


def downgrade():
    op.drop_index('ix_documento_assembleia_id', table_name='documento')
    op.drop_constraint('documento_assembleia_id_fkey', 'documento', type_='foreignkey')
    op.drop_column('documento', 'assembleia_id')
