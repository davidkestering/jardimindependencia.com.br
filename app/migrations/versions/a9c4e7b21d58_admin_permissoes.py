"""usuários da administração: áreas viram permissões por ação ({area: [acoes]})

Revision ID: a9c4e7b21d58
Revises: d8e3f9a52b07
"""
import json

from alembic import op
import sqlalchemy as sa

revision = 'a9c4e7b21d58'
down_revision = 'd8e3f9a52b07'
branch_labels = None
depends_on = None


def upgrade():
    from models import permissoes_totais  # quem tinha a área ganha todas as ações dela: nada muda para os usuários atuais
    op.alter_column('admin_user', 'areas', server_default=sa.text("'{}'::jsonb"))
    con = op.get_bind()
    for uid, areas in con.execute(sa.text("SELECT id, areas FROM admin_user WHERE jsonb_typeof(areas) = 'array'")):
        con.execute(sa.text("UPDATE admin_user SET areas = CAST(:p AS jsonb) WHERE id = :id"), {"p": json.dumps(permissoes_totais(areas)), "id": uid})


def downgrade():
    con = op.get_bind()
    for uid, areas in con.execute(sa.text("SELECT id, areas FROM admin_user WHERE jsonb_typeof(areas) = 'object'")):
        con.execute(sa.text("UPDATE admin_user SET areas = CAST(:p AS jsonb) WHERE id = :id"), {"p": json.dumps(list(areas)), "id": uid})
    op.alter_column('admin_user', 'areas', server_default=sa.text("'[]'::jsonb"))
