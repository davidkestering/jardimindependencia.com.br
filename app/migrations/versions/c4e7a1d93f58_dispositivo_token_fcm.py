"""token de push sem tamanho fixo (FCM do app Android); unicidade pelo md5, porque o btree não indexa texto longo

Revision ID: c4e7a1d93f58
Revises: b5c8d2e47a19
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e7a1d93f58'
down_revision = 'b5c8d2e47a19'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint('dispositivo_app_token_key', 'dispositivo_app', type_='unique')
    op.alter_column('dispositivo_app', 'token', type_=sa.Text())
    op.execute("CREATE UNIQUE INDEX uq_dispositivo_app_token ON dispositivo_app (md5(token))")


def downgrade():
    op.execute("DELETE FROM dispositivo_app WHERE length(token) > 200")  # tokens longos não cabem de volta; o app registra de novo no login
    op.drop_index('uq_dispositivo_app_token', table_name='dispositivo_app')
    op.alter_column('dispositivo_app', 'token', type_=sa.String(length=200))
    op.create_unique_constraint('dispositivo_app_token_key', 'dispositivo_app', ['token'])
