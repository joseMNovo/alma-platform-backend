from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.push_subscription import PushSubscription
from app.schemas.push import SubscribeRequest, UnsubscribeRequest, PushSubscriptionOut
from app.utils.logger import log_info, log_error

router = APIRouter()


@router.post("/subscribe", response_model=PushSubscriptionOut, status_code=201)
def subscribe(req: SubscribeRequest, db: Session = Depends(get_db)):
    """Registra (o actualiza) la suscripción push de un dispositivo.

    Upsert por endpoint: si el mismo navegador se re-suscribe, actualizamos
    sus claves y su dueño en vez de duplicar la fila.
    """
    try:
        existing = (
            db.query(PushSubscription)
            .filter(PushSubscription.endpoint == req.endpoint)
            .first()
        )
        if existing:
            existing.user_type = req.user_type
            existing.user_id = req.user_id
            existing.p256dh = req.keys.p256dh
            existing.auth = req.keys.auth
            existing.user_agent = req.user_agent
            db.commit()
            db.refresh(existing)
            return existing

        sub = PushSubscription(
            user_type=req.user_type,
            user_id=req.user_id,
            endpoint=req.endpoint,
            p256dh=req.keys.p256dh,
            auth=req.keys.auth,
            user_agent=req.user_agent,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        log_info("Suscripción push creada", module="push", action="subscribe", user=req.user_id, meta={"user_type": req.user_type})
        return sub
    except Exception:
        db.rollback()
        log_error("Error al crear suscripción push", module="push", action="subscribe", exc_info=True)
        raise


@router.post("/unsubscribe", status_code=204)
def unsubscribe(req: UnsubscribeRequest, db: Session = Depends(get_db)):
    """Borra una suscripción por su endpoint (idempotente)."""
    try:
        db.query(PushSubscription).filter(PushSubscription.endpoint == req.endpoint).delete(synchronize_session=False)
        db.commit()
        log_info("Suscripción push eliminada", module="push", action="unsubscribe")
    except Exception:
        db.rollback()
        log_error("Error al eliminar suscripción push", module="push", action="unsubscribe", exc_info=True)
        raise
