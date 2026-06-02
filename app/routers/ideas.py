from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.models.idea import Idea as IdeaModel
from app.models.idea_comment import IdeaComment as IdeaCommentModel
from app.schemas.idea import Idea, IdeaCreate, IdeaUpdate, IdeaCommentCreate, IdeaCommentOut
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _serialize_idea(idea: IdeaModel) -> dict:
    """Serializa una idea incluyendo nombre del creador y cantidad de comentarios."""
    creator_name = None
    if idea.created_by is not None:
        creator_name = f"{idea.created_by.name or ''} {idea.created_by.last_name or ''}".strip() or None
    return {
        "id": idea.id,
        "title": idea.title,
        "body": idea.body,
        "category": idea.category,
        "created_by_volunteer_id": idea.created_by_volunteer_id,
        "created_by_name": creator_name,
        "comment_count": idea.comments.count(),
        "created_at": idea.created_at,
        "updated_at": idea.updated_at,
    }


def _serialize_comment(c: IdeaCommentModel) -> dict:
    """Serializa un comentario incluyendo nombre del voluntario."""
    volunteer_name = None
    if c.volunteer is not None:
        volunteer_name = f"{c.volunteer.name or ''} {c.volunteer.last_name or ''}".strip() or None
    return {
        "id": c.id,
        "idea_id": c.idea_id,
        "volunteer_id": c.volunteer_id,
        "volunteer_name": volunteer_name,
        "body": c.body,
        "created_at": c.created_at,
    }


# ── Ideas ──────────────────────────────────────────────────────────────

@router.get("/", response_model=List[Idea])
def list_ideas(
    skip: int = 0,
    limit: int = 500,
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(IdeaModel)
    if category is not None:
        q = q.filter(IdeaModel.category == category)
    ideas = q.order_by(IdeaModel.created_at.desc()).offset(skip).limit(limit).all()
    return [_serialize_idea(i) for i in ideas]


@router.get("/categories", response_model=List[str])
def list_categories(db: Session = Depends(get_db)):
    """Devuelve las categorías distintas que existen en ideas (sin nulls)."""
    rows = (
        db.query(IdeaModel.category)
        .filter(IdeaModel.category.isnot(None), IdeaModel.category != "")
        .distinct()
        .order_by(IdeaModel.category)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/{id}", response_model=Idea)
def get_idea(id: int, db: Session = Depends(get_db)):
    idea = db.query(IdeaModel).filter(IdeaModel.id == id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    return _serialize_idea(idea)


@router.post("/", response_model=Idea, status_code=201)
def create_idea(data: IdeaCreate, db: Session = Depends(get_db)):
    try:
        idea = IdeaModel(**data.model_dump())
        db.add(idea)
        db.commit()
        db.refresh(idea)
        log_info("Idea creada", module="ideas", action="create_idea", user=data.created_by_volunteer_id, meta={"id": idea.id, "title": idea.title})
        return _serialize_idea(idea)
    except Exception:
        log_error("Error al crear idea", module="ideas", action="create_idea", exc_info=True)
        raise


@router.put("/{id}", response_model=Idea)
def update_idea(id: int, data: IdeaUpdate, db: Session = Depends(get_db)):
    idea = db.query(IdeaModel).filter(IdeaModel.id == id).first()
    if not idea:
        log_warn("Idea no encontrada para editar", module="ideas", action="edit_idea", meta={"id": id})
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    try:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(idea, key, value)
        db.commit()
        db.refresh(idea)
        log_info("Idea actualizada", module="ideas", action="edit_idea", meta={"id": id})
        return _serialize_idea(idea)
    except Exception:
        log_error("Error al actualizar idea", module="ideas", action="edit_idea", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_idea(id: int, db: Session = Depends(get_db)):
    idea = db.query(IdeaModel).filter(IdeaModel.id == id).first()
    if not idea:
        log_warn("Idea no encontrada para eliminar", module="ideas", action="delete_idea", meta={"id": id})
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    try:
        db.delete(idea)
        db.commit()
        log_info("Idea eliminada", module="ideas", action="delete_idea", meta={"id": id})
    except Exception:
        log_error("Error al eliminar idea", module="ideas", action="delete_idea", meta={"id": id}, exc_info=True)
        raise


# ── Comentarios ────────────────────────────────────────────────────────

@router.get("/{idea_id}/comments", response_model=List[IdeaCommentOut])
def list_comments(idea_id: int, db: Session = Depends(get_db)):
    if not db.query(IdeaModel).filter(IdeaModel.id == idea_id).first():
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    comments = (
        db.query(IdeaCommentModel)
        .filter(IdeaCommentModel.idea_id == idea_id)
        .order_by(IdeaCommentModel.created_at.asc())
        .all()
    )
    return [_serialize_comment(c) for c in comments]


@router.post("/{idea_id}/comments", response_model=IdeaCommentOut, status_code=201)
def create_comment(idea_id: int, data: IdeaCommentCreate, volunteer_id: int = Query(...), db: Session = Depends(get_db)):
    if not db.query(IdeaModel).filter(IdeaModel.id == idea_id).first():
        log_warn("Idea no encontrada al crear comentario", module="ideas", action="create_comment", meta={"idea_id": idea_id})
        raise HTTPException(status_code=404, detail="Idea no encontrada")
    try:
        comment = IdeaCommentModel(
            idea_id=idea_id,
            volunteer_id=volunteer_id,
            body=data.body,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        log_info("Comentario creado", module="ideas", action="create_comment", user=volunteer_id, meta={"id": comment.id, "idea_id": idea_id})
        return _serialize_comment(comment)
    except Exception:
        log_error("Error al crear comentario", module="ideas", action="create_comment", meta={"idea_id": idea_id}, exc_info=True)
        raise


@router.delete("/{idea_id}/comments/{comment_id}", status_code=204)
def delete_comment(idea_id: int, comment_id: int, db: Session = Depends(get_db)):
    comment = db.query(IdeaCommentModel).filter(
        IdeaCommentModel.id == comment_id,
        IdeaCommentModel.idea_id == idea_id,
    ).first()
    if not comment:
        log_warn("Comentario no encontrado para eliminar", module="ideas", action="delete_comment", meta={"idea_id": idea_id, "comment_id": comment_id})
        raise HTTPException(status_code=404, detail="Comentario no encontrado")
    try:
        db.delete(comment)
        db.commit()
        log_info("Comentario eliminado", module="ideas", action="delete_comment", meta={"id": comment_id, "idea_id": idea_id})
    except Exception:
        log_error("Error al eliminar comentario", module="ideas", action="delete_comment", meta={"id": comment_id, "idea_id": idea_id}, exc_info=True)
        raise
