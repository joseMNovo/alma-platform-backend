"""Puesto de venta del stand.

La mercadería vive en `inventario`. Esta tabla (`stand_products`) es la
GÓNDOLA: cuáles de esos ítems están a la venta, a qué precio y en qué orden.

Durante la jornada el puesto tuvo catálogo propio, con su propio stock. Cumplió,
pero el precio era que la misma mercadería estaba en dos lugares que no se
hablaban: vendías 13 mates acá y el inventario seguía diciendo que estaban todos.

Qué cambió con la unión:

* **El stock sale de `inventario.quantity`.** Vender descuenta ahí; anular
  devuelve. La regla vieja —"el stock se calcula, nunca se guarda"— nació para
  que no hubiera DOS contadores que se separaran. Ahora hay uno solo, así que
  ya no aplica; y a cambio se gana poder corregirlo a mano cuando el conteo
  físico no coincide, que antes era imposible.
* **La caja se sigue calculando** sobre las ventas no anuladas. Eso no cambia.
* **Vender valida stock.** Antes no: se podían vender 50 mates habiendo 13.
  Con el inventario de por medio eso lo dejaría en negativo.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from decimal import Decimal
from datetime import date

from app.database import get_db
from app.models.stand import StandProduct, StandSale, StandSaleItem
from app.models.inventario import Inventario
from app.schemas.stand import (
    StandProductCreate, StandProductUpdate, StandProductOut,
    StandSaleCreate, StandSaleOut, StandSummary,
)
from app.utils.logger import log_info, log_warn, log_error
from app.utils.timezone import AR_TZ

router = APIRouter()


def _con_huso(dt):
    """Devuelve el timestamp convertido a hora de Argentina y con el huso puesto.

    MySQL entrega los TIMESTAMP como fechas "peladas" calculadas con el reloj
    del VPS, que corre en Europe/Berlin (ver app/utils/timezone.py). Sin huso,
    el navegador las toma como locales y una venta de las 15:49 se mostraba a
    las 20:49.

    `astimezone(AR_TZ)` interpreta la fecha naive como hora local del servidor
    —que es justo el reloj con el que MySQL la calculó— y la pasa a Argentina.
    Así viaja `...-03:00` y el teléfono no tiene que adivinar nada. Se apoya en
    el AR_TZ que ya usa el calendario para no tener dos definiciones de "la
    hora de acá" que puedan separarse.
    """
    return dt.astimezone(AR_TZ) if dt is not None else None


def _vendidos_por_producto(db: Session) -> dict:
    """Unidades vendidas por producto, sin contar las ventas anuladas."""
    filas = (
        db.query(StandSaleItem.product_id, func.sum(StandSaleItem.quantity))
        .join(StandSale, StandSale.id == StandSaleItem.sale_id)
        .filter(StandSale.is_void == 0)
        .group_by(StandSaleItem.product_id)
        .all()
    )
    return {pid: int(cant or 0) for pid, cant in filas}


def _armar_producto(p: StandProduct, vendidos: int) -> StandProductOut:
    item = p.inventory_item
    return StandProductOut(
        id=p.id,
        # El nombre lo manda el inventario: es la madre. Se mantienen iguales
        # al editar, pero si alguna vez se separan, gana el ítem.
        name=item.name if item else p.name,
        inventory_item_id=p.inventory_item_id,
        unit_price=p.unit_price,
        is_active=bool(p.is_active),
        sort_order=p.sort_order,
        sold=vendidos,
        # Enganchado: lo que queda es lo que dice el inventario. Sin enganchar
        # (productos anteriores a sql/25): el cálculo de antes.
        stock=item.quantity if item else p.initial_stock - vendidos,
    )


# -- Productos ---------------------------------------------------------

@router.get("/products", response_model=List[StandProductOut])
def list_products(incluir_inactivos: bool = Query(False), db: Session = Depends(get_db)):
    q = db.query(StandProduct)
    if not incluir_inactivos:
        q = q.filter(StandProduct.is_active == 1)
    productos = q.order_by(StandProduct.sort_order, StandProduct.name).all()

    vendidos = _vendidos_por_producto(db)
    return [_armar_producto(p, vendidos.get(p.id, 0)) for p in productos]


@router.post("/products", response_model=StandProductOut, status_code=201)
def create_product(data: StandProductCreate, db: Session = Depends(get_db)):
    """Alta desde el puesto: crea el ítem en el inventario Y lo pone en góndola.

    Son las dos caras de lo mismo. Si solo creara la fila de góndola, volvería
    el problema que la unión vino a resolver: mercadería que se vende y que el
    inventario no sabe que existe.
    """
    try:
        item = Inventario(
            name=data.name,
            category=data.category or "Merchandising",
            quantity=max(data.quantity, 0),
            # Sin alerta de stock bajo por defecto: el mínimo lo pone quien
            # conozca el ítem, desde el módulo Inventario.
            minimum_stock=0,
            # Cuánto vale, que no es a cuánto se vende. Nadie lo sabe al
            # cargarlo apurado en un stand, así que queda en 0.
            price=Decimal("0"),
            entry_date=date.today(),
        )
        db.add(item)
        db.flush()

        p = StandProduct(
            name=data.name,
            inventory_item_id=item.id,
            unit_price=data.unit_price,
            # Vestigial para los enganchados: el stock lo lleva el inventario.
            initial_stock=0,
            is_active=1 if data.is_active else 0,
            sort_order=data.sort_order,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        log_info("Producto de stand creado", module="stand", action="create_product",
                 meta={"id": p.id, "inventory_item_id": item.id})
        return _armar_producto(p, 0)
    except Exception:
        db.rollback()
        log_error("Error al crear producto de stand", module="stand", action="create_product", exc_info=True)
        raise


@router.put("/products/{product_id}", response_model=StandProductOut)
def update_product(product_id: int, data: StandProductUpdate, db: Session = Depends(get_db)):
    p = db.query(StandProduct).filter(StandProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    try:
        cambios = data.model_dump(exclude_unset=True)

        # `quantity` no es una columna de la góndola: corrige el stock del ítem
        # en el inventario, que es donde vive.
        cantidad = cambios.pop("quantity", None)
        if cantidad is not None:
            if not p.inventory_item:
                raise HTTPException(
                    status_code=409,
                    detail="El producto no está enganchado al inventario todavía",
                )
            p.inventory_item.quantity = max(int(cantidad), 0)

        # El nombre se guarda en los dos lados para que las ventas viejas
        # sigan diciendo cómo se llamaba lo que se vendió.
        if "name" in cambios and p.inventory_item:
            p.inventory_item.name = cambios["name"]

        for k, v in cambios.items():
            setattr(p, k, v)
        db.commit()
        db.refresh(p)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        log_error("Error al actualizar producto de stand", module="stand", action="edit_product", exc_info=True)
        raise
    return _armar_producto(p, _vendidos_por_producto(db).get(p.id, 0))


@router.delete("/products/{product_id}")
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    """Baja LÓGICA: borrarlo de verdad dejaría ventas apuntando a la nada."""
    p = db.query(StandProduct).filter(StandProduct.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    p.is_active = 0
    db.commit()
    log_info("Producto de stand desactivado", module="stand", action="deactivate_product", meta={"id": product_id})
    return {"ok": True}


# -- Ventas ------------------------------------------------------------

def _serializar_venta(venta: StandSale, productos: Optional[dict] = None) -> StandSaleOut:
    items = []
    for it in venta.items:
        nombre = None
        if productos and it.product_id in productos:
            nombre = productos[it.product_id].name
        elif it.product is not None:
            nombre = it.product.name
        items.append({
            "product_id": it.product_id,
            "product_name": nombre,
            "quantity": it.quantity,
            "unit_price": it.unit_price,
        })
    return StandSaleOut(
        id=venta.id,
        payment_method=venta.payment_method,
        total=venta.total,
        notes=venta.notes,
        sold_by_volunteer_id=venta.sold_by_volunteer_id,
        customer_name=venta.customer_name,
        customer_email=venta.customer_email,
        is_void=bool(venta.is_void),
        created_at=_con_huso(venta.created_at),
        items=items,
    )


@router.post("/sales", response_model=StandSaleOut, status_code=201)
def create_sale(data: StandSaleCreate, db: Session = Depends(get_db)):
    """Registra una venta.

    El precio sale de la BASE, nunca del body: si lo mandara el navegador,
    quien cobra podría enviar cualquier número y la caja no cerraría jamás.
    """
    ids = [i.product_id for i in data.items]
    productos = {p.id: p for p in db.query(StandProduct).filter(StandProduct.id.in_(ids)).all()}
    faltantes = [i for i in ids if i not in productos]
    if faltantes:
        raise HTTPException(status_code=404, detail="Hay un producto que ya no existe")

    # Cuánto se lleva de cada ítem del inventario. Se acumula por ítem y no por
    # renglón porque el mismo producto puede venir dos veces en la misma venta.
    pedido: dict = {}
    for it in data.items:
        inv_id = productos[it.product_id].inventory_item_id
        if inv_id:
            pedido[inv_id] = pedido.get(inv_id, 0) + it.quantity

    items_inv: dict = {}
    if pedido:
        # FOR UPDATE: dos voluntarios cobrando la última unidad al mismo tiempo
        # leerían el mismo stock y las dos ventas pasarían. Con el lock, la
        # segunda espera y ve el número ya descontado.
        items_inv = {
            i.id: i
            for i in db.query(Inventario)
            .filter(Inventario.id.in_(list(pedido.keys())))
            .with_for_update()
            .all()
        }
        sin_stock = [
            items_inv[iid].name
            for iid, cant in pedido.items()
            if iid in items_inv and items_inv[iid].quantity < cant
        ]
        if sin_stock:
            # Se suelta el lock antes de contestar: si no, queda tomado hasta
            # que la sesión se cierre sola.
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"No hay stock suficiente de: {', '.join(sin_stock)}",
            )

    try:
        venta = StandSale(
            payment_method=data.payment_method,
            total=Decimal("0"),
            sold_by_volunteer_id=data.sold_by_volunteer_id,
            notes=data.notes,
            customer_name=data.customer_name,
            customer_email=data.customer_email,
        )
        db.add(venta)
        db.flush()

        total = Decimal("0")
        for item in data.items:
            p = productos[item.product_id]
            precio = Decimal(str(p.unit_price))
            total += precio * item.quantity
            db.add(StandSaleItem(
                sale_id=venta.id,
                product_id=p.id,
                quantity=item.quantity,
                unit_price=precio,
            ))

        venta.total = total

        # El descuento va en la MISMA transacción que la venta: o quedan las
        # dos cosas o no queda ninguna. Si se hiciera aparte, un error entre
        # medio dejaría plata cobrada sin mercadería descontada.
        for iid, cant in pedido.items():
            if iid in items_inv:
                items_inv[iid].quantity -= cant

        db.commit()
        db.refresh(venta)
        log_info("Venta de stand registrada", module="stand", action="create_sale",
                 meta={"id": venta.id, "total": str(total), "medio": venta.payment_method})
    except Exception:
        db.rollback()
        log_error("Error al registrar venta de stand", module="stand", action="create_sale", exc_info=True)
        raise

    return _serializar_venta(venta, productos)


@router.get("/sales", response_model=List[StandSaleOut])
def list_sales(limit: int = Query(50), incluir_anuladas: bool = Query(True), db: Session = Depends(get_db)):
    q = db.query(StandSale)
    if not incluir_anuladas:
        q = q.filter(StandSale.is_void == 0)
    ventas = q.order_by(StandSale.created_at.desc(), StandSale.id.desc()).limit(limit).all()
    return [_serializar_venta(v) for v in ventas]


@router.post("/sales/{sale_id}/void", response_model=StandSaleOut)
def void_sale(sale_id: int, db: Session = Depends(get_db)):
    """Anula una venta mal cargada. No se borra: la caja tiene que poder
    explicar por qué un número cambió a mitad de la jornada."""
    venta = db.query(StandSale).filter(StandSale.id == sale_id).first()
    if not venta:
        raise HTTPException(status_code=404, detail="Venta no encontrada")

    # Anular una venta ya anulada devolvería el stock una segunda vez. La caja
    # ya la estaba ignorando, así que el único efecto sería inflar el inventario
    # con mercadería que no existe.
    if venta.is_void:
        return _serializar_venta(venta)

    try:
        ids = [r.product_id for r in venta.items]
        productos = {
            p.id: p for p in db.query(StandProduct).filter(StandProduct.id.in_(ids)).all()
        }

        # Lo vendido vuelve al inventario: si la venta no ocurrió, la
        # mercadería nunca salió.
        devolver: dict = {}
        for r in venta.items:
            p = productos.get(r.product_id)
            if p and p.inventory_item_id:
                devolver[p.inventory_item_id] = devolver.get(p.inventory_item_id, 0) + r.quantity

        if devolver:
            for item in (
                db.query(Inventario)
                .filter(Inventario.id.in_(list(devolver.keys())))
                .with_for_update()
                .all()
            ):
                item.quantity += devolver[item.id]

        venta.is_void = 1
        db.commit()
        db.refresh(venta)
    except Exception:
        db.rollback()
        log_error("Error al anular venta de stand", module="stand", action="void_sale",
                  meta={"id": sale_id}, exc_info=True)
        raise

    log_warn("Venta de stand anulada", module="stand", action="void_sale",
             meta={"id": sale_id, "devuelto": devolver})
    return _serializar_venta(venta)


# -- Caja --------------------------------------------------------------

@router.get("/summary", response_model=StandSummary)
def summary(db: Session = Depends(get_db)):
    vivas = db.query(StandSale).filter(StandSale.is_void == 0).all()
    total = sum((Decimal(str(v.total)) for v in vivas), Decimal("0"))
    efectivo = sum((Decimal(str(v.total)) for v in vivas if v.payment_method == "efectivo"), Decimal("0"))

    filas = (
        db.query(
            StandSaleItem.product_id,
            StandProduct.name,
            func.sum(StandSaleItem.quantity),
            func.sum(StandSaleItem.quantity * StandSaleItem.unit_price),
        )
        .join(StandSale, StandSale.id == StandSaleItem.sale_id)
        .join(StandProduct, StandProduct.id == StandSaleItem.product_id)
        .filter(StandSale.is_void == 0)
        .group_by(StandSaleItem.product_id, StandProduct.name)
        .all()
    )

    return StandSummary(
        total=total,
        efectivo=efectivo,
        transferencia=total - efectivo,
        sales_count=len(vivas),
        by_product=[
            {"product_id": pid, "name": nombre, "units": int(u or 0), "revenue": Decimal(str(r or 0))}
            for pid, nombre, u, r in filas
        ],
    )
