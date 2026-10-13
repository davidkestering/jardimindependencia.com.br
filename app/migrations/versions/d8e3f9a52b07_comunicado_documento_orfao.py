"""documento de comunicado enviado antes de c7d2e8f41a96 (ficou só no disco, em uploads/comunicados/): vira Documento da
categoria Comunicados, movido para uploads/documentos/, ligado ao comunicado. Nome original vem do histórico ("Comunicado criado").

Revision ID: d8e3f9a52b07
Revises: c7d2e8f41a96
"""
import os
import re
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = 'd8e3f9a52b07'
down_revision = 'c7d2e8f41a96'
branch_labels = None
depends_on = None

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))


def upgrade():
    pasta = UPLOAD_DIR / "comunicados"
    if not pasta.is_dir():
        return
    conn = op.get_bind()
    for arq in sorted(pasta.iterdir()):
        m = re.fullmatch(r"([0-9a-f-]{36})_(\d{8}_\d{6})(\.pdf|\.docx)", arq.name)
        if not m:
            continue
        cid, carimbo, ext = m.groups()
        c = conn.execute(sa.text("select titulo, visibilidade, autor, criado_ip, criado_em, documento_id from comunicado where id = :id"), {"id": cid}).first()
        if not c or c.documento_id:
            continue
        nome = conn.scalar(sa.text("select detalhe->>'documento' from historico where acao = 'Comunicado criado' and detalhe->>'titulo' = :t "
                                   "and detalhe->>'documento' is not null order by quando desc limit 1"), {"t": c.titulo}) or arq.name
        doc_id = conn.scalar(sa.text("select uuidv7()"))
        destino = f"documentos/{doc_id}_{carimbo}{ext}"
        conn.execute(sa.text("insert into documento (id, titulo, categoria, arquivo, nome_original, publico, competencia, criado_em, enviado_por, enviado_ip) "
                             "values (:id, :titulo, 'Comunicados', :arquivo, :nome, :publico, (:criado at time zone 'America/Belem')::date, :criado, :por, :ip)"),
                     {"id": doc_id, "titulo": c.titulo, "arquivo": destino, "nome": nome[:255], "publico": c.visibilidade != "rascunho",
                      "criado": c.criado_em, "por": c.autor, "ip": c.criado_ip})
        conn.execute(sa.text("update comunicado set documento_id = :d where id = :id"), {"d": doc_id, "id": cid})
        (UPLOAD_DIR / "documentos").mkdir(parents=True, exist_ok=True)
        arq.replace(UPLOAD_DIR / destino)


def downgrade():
    pass  # o Documento criado fica: é a situação correta do comunicado
