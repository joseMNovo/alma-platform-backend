"""Inventario: la madre de la mercadería.

Guarda qué tenemos. Cuáles de esos ítems están a la venta lo dice la góndola
(`stand_products`), no una columna de acá: "está a la venta" es tener fila
activa allá. Con un `for_sale` propio habría dos lugares contestando la misma
pregunta y en algún momento contestarían distinto.

Igual el formulario pide un solo check, y este router se encarga de crear,
reactivar o dar de baja la fila de góndola. Que sean dos tablas es un detalle
de cómo está guardado, no algo que el usuario tenga que saber.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from typing import List, Optional
from decimal import Decimal

from app.database import get_db
from app.models.inventario import Inventario as InventarioModel
from app.models.stand import StandProduct, StandSale, StandSaleItem
from app.schemas.inventario import Inventario, InventarioCreate, InventarioUpdate
from app.utils.logger import log_info, log_warn, log_error

router = APIRouter()


def _gondolas(db: Session, item_ids: List[int]) -> dict:
    """Fila de góndola de cada ítem, esté activa o no."""
    if not item_ids:
        return {}
    filas = (
        db.query(StandProduct)
        .filter(StandProduct.inventory_item_id.in_(item_ids))
        .all()
    )
    return {g.inventory_item_id: g for g in filas}


def _vendidos(db: Session, item_ids: List[int]) -> dict:
    """Unidades vendidas por ítem del inventario, sin contar ventas anuladas."""
    if not item_ids:
        return {}
    filas = (
        db.query(StandProduct.inventory_item_id, func.sum(StandSaleItem.quantity))
        .join(StandSaleItem, StandSaleItem.product_id == StandProduct.id)
        .join(StandSale, StandSale.id == StandSaleItem.sale_id)
        .filter(StandSale.is_void == 0, StandProduct.inventory_item_id.in_(item_ids))
        .group_by(StandProduct.inventory_item_id)
        .all()
    )
    return {iid: int(cant or 0) for iid, cant in filas}


def _armar(item: InventarioModel, gondola: Optional[StandProduct], vendidos: int) -> Inventario:
    return Inventario(
        **{c.name: getattr(item, c.name) for c in InventarioModel.__table__.columns},
        for_sale=bool(gondola and gondola.is_active),
        # El precio se devuelve aunque esté fuera de góndola: si lo vuelven a
        # poner a la venta, no hay que acordarse a cuánto se vendía.
        sale_price=gondola.unit_price if gondola else None,
        sold=vendidos,
    )


def _sincronizar_gondola(
    db: Session,
    item: InventarioModel,
    for_sale: Optional[bool],
    sale_price: Optional[Decimal],
) -> None:
    """Pone o saca el ítem de la góndola según el check del formulario.

    Sacar es baja LÓGICA, nunca borrar la fila: se perdería el orden, y el
    historial de ventas quedaría apuntando a la nada (la FK es RESTRICT, así
    que ni siquiera dejaría).
    """
    if for_sale is None and sale_price is None:
        return

    gondola = (
        db.query(StandProduct)
        .filter(StandProduct.inventory_item_id == item.id)
        .first()
    )

    if for_sale is False:
        if gondola:
            gondola.is_active = 0
        return

    if gondola is None:
        # Solo se crea si de verdad lo están poniendo a la venta. Mandar un
        # precio sin tildar el check no tiene que crear nada.
        if not for_sale:
            return
        ultimo = db.query(func.max(StandProduct.sort_order)).scalar() or 0
        gondola = StandProduct(
            name=item.name,
            inventory_item_id=item.id,
            unit_price=sale_price or Decimal("0"),
            # El stock lo lleva el inventario; esta columna quedó como historia
            # de los productos anteriores a la unión.
            initial_stock=0,
            is_active=1,
            sort_order=ultimo + 1,
        )
        db.add(gondola)
        return

    if for_sale:
        gondola.is_active = 1
    if sale_price is not None:
        gondola.unit_price = sale_price
    # El nombre lo manda el inventario.
    gondola.name = item.name


@router.get("/", response_model=List[Inventario])
def list_inventario(
    skip: int = 0,
    limit: int = 500,
    category: Optional[str] = Query(None),
    assigned_volunteer_id: Optional[int] = Query(None),
    # true = solo lo que está en góndola; false = solo lo que no.
    for_sale: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(InventarioModel)
    if category is not None:
        q = q.filter(InventarioModel.category == category)
    if assigned_volunteer_id is not None:
        q = q.filter(InventarioModel.assigned_volunteer_id == assigned_volunteer_id)

    if for_sale is not None:
        en_gondola = select(StandProduct.inventory_item_id).where(
            StandProduct.is_active == 1,
            StandProduct.inventory_item_id.isnot(None),
        )
        cond = InventarioModel.id.in_(en_gondola)
        q = q.filter(cond if for_sale else ~cond)

    items = q.offset(skip).limit(limit).all()
    ids = [i.id for i in items]
    gondolas, vendidos = _gondolas(db, ids), _vendidos(db, ids)
    return [_armar(i, gondolas.get(i.id), vendidos.get(i.id, 0)) for i in items]


@router.get("/{id}", response_model=Inventario)
def get_inventario(id: int, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    return _armar(item, _gondolas(db, [id]).get(id), _vendidos(db, [id]).get(id, 0))


@router.post("/", response_model=Inventario, status_code=201)
def create_inventario(data: InventarioCreate, db: Session = Depends(get_db)):
    try:
        campos = data.model_dump()
        for_sale = campos.pop("for_sale", False)
        sale_price = campos.pop("sale_price", None)

        item = InventarioModel(**campos)
        db.add(item)
        db.flush()

        _sincronizar_gondola(db, item, for_sale, sale_price)

        db.commit()
        db.refresh(item)
        log_info("Ítem de inventario creado", module="inventario", action="create_item",
                 meta={"id": item.id, "name": item.name, "for_sale": bool(for_sale)})
        return _armar(item, _gondolas(db, [item.id]).get(item.id), 0)
    except Exception:
        db.rollback()
        log_error("Error al crear ítem de inventario", module="inventario", action="create_item", exc_info=True)
        raise


@router.put("/{id}", response_model=Inventario)
def update_inventario(id: int, data: InventarioUpdate, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        log_warn("Ítem de inventario no encontrado para editar", module="inventario", action="edit_item", meta={"id": id})
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")
    try:
        cambios = data.model_dump(exclude_unset=True)
        for_sale = cambios.pop("for_sale", None)
        sale_price = cambios.pop("sale_price", None)

        for key, value in cambios.items():
            setattr(item, key, value)

        _sincronizar_gondola(db, item, for_sale, sale_price)

        db.commit()
        db.refresh(item)
        log_info("Ítem de inventario actualizado", module="inventario", action="edit_item", meta={"id": id})
        return _armar(item, _gondolas(db, [id]).get(id), _vendidos(db, [id]).get(id, 0))
    except Exception:
        db.rollback()
        log_error("Error al actualizar ítem de inventario", module="inventario", action="edit_item", meta={"id": id}, exc_info=True)
        raise


@router.delete("/{id}", status_code=204)
def delete_inventario(id: int, db: Session = Depends(get_db)):
    item = db.query(InventarioModel).filter(InventarioModel.id == id).first()
    if not item:
        log_warn("Ítem de inventario no encontrado para eliminar", module="inventario", action="delete_item", meta={"id": id})
        raise HTTPException(status_code=404, detail="Ítem de inventario no encontrado")

    # Lo que impide borrar NO es estar en la góndola: es tener ventas.
    #
    # La primera versión de esto bloqueaba con solo existir la fila de góndola,
    # y mandaba a destildar "Para vender" para poder borrar. Pero destildar es
    # baja LÓGICA: la fila queda con is_active = 0, así que el ítem no se podía
    # borrar nunca y el mensaje pedía algo que no servía de nada.
    #
    # Se cuentan TODAS las líneas de venta, anuladas incluidas: la foreign key
    # de `stand_sale_items` no distingue, y es ella la que manda acá.
    gondolas = db.query(StandProduct).filter(StandProduct.inventory_item_id == id).all()
    con_ventas = [
        g for g in gondolas
        if db.query(StandSaleItem.id).filter(StandSaleItem.product_id == g.id).first()
    ]
    if con_ventas:
        raise HTTPException(
            status_code=409,
            detail=(
                "Este ítem tiene ventas registradas en el puesto y borrarlo se "
                "llevaría ese historial. Si no querés que siga a la venta, "
                "destildá 'Para vender': lo saca de la góndola y conserva las ventas."
            ),
        )

    try:
        # Sin ventas colgando, la fila de góndola no sostiene nada: se va con
        # el ítem. Dejarla huérfana apuntando a un inventario que ya no existe
        # es justo lo que la FK está para impedir.
        for g in gondolas:
            db.delete(g)
        db.flush()

        db.delete(item)
        db.commit()
        log_info("Ítem de inventario eliminado", module="inventario", action="delete_item", meta={"id": id})
    except Exception:
        db.rollback()
        log_error("Error al eliminar ítem de inventario", module="inventario", action="delete_item", meta={"id": id}, exc_info=True)
        raise
