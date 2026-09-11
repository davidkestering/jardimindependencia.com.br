"""usuários da administração: master e áreas liberadas

Revision ID: f2c7a9e13b64
Revises: e5b1c8d40a72
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f2c7a9e13b64'
down_revision = 'e5b1c8d40a72'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('admin_user', sa.Column('master', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('admin_user', sa.Column('areas', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.execute("UPDATE admin_user SET master = true WHERE login IN ('ingrid.vinagre', 'david.kestering')")
    op.execute("""UPDATE admin_user SET areas = '["moradores","documentos","comunicados","financeiro","assembleias","interfone"]'::jsonb WHERE login = 'admin'""")


def downgrade():
    op.drop_column('admin_user', 'areas')
    op.drop_column('admin_user', 'master')
