"""Entrega de arquivos enviados (documentos), sempre atrás de checagem de sessão."""
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import UPLOAD_DIR
from models import Documento


def servir_documento(db: Session, doc_id: str, apenas_publicos: bool):
    try:
        d = db.get(Documento, uuid.UUID(doc_id))
    except ValueError:
        d = None
    if not d or (apenas_publicos and (not d.publico or d.excluido_em)):
        raise HTTPException(404)
    caminho = (Path(UPLOAD_DIR) / d.arquivo).resolve()
    if not str(caminho).startswith(str(Path(UPLOAD_DIR).resolve())) or not caminho.is_file():
        raise HTTPException(404)
    return FileResponse(caminho, filename=d.nome_original)
