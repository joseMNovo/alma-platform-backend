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
import base64
from decimal import Decimal
from datetime import date, datetime, time, timedelta

from app.database import get_db
from app.models.stand import StandProduct, StandSale, StandSaleItem
from app.models.inventario import Inventario
from app.schemas.stand import (
    StandProductCreate, StandProductUpdate, StandProductOut,
    StandSaleCreate, StandSaleOut, StandSummary, StandSyncResult,
)
from app.utils.logger import log_info, log_warn, log_error
from app.services import reportes
from app.utils.timezone import AR_TZ, ar_date_to_server

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
    # Si esta venta ya entró, se devuelve la que hay en vez de crear otra.
    # Pasa de verdad: el POST llega, la respuesta se pierde en el camino, y el
    # teléfono reintenta creyendo que falló.
    if data.client_uuid:
        ya = db.query(StandSale).filter(StandSale.client_uuid == data.client_uuid).first()
        if ya:
            log_info("Venta repetida ignorada", module="stand", action="create_sale_repetida",
                     meta={"client_uuid": data.client_uuid, "id": ya.id})
            return _serializar_venta(ya, {})

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
            client_uuid=data.client_uuid,
            origen="vivo",
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


def _a_hora_servidor(dt: datetime) -> datetime:
    """Pasa la fecha que mandó el teléfono al reloj con el que guarda MySQL.

    Los TIMESTAMP de la base son fechas "peladas" en hora del VPS, que corre en
    Europe/Berlin. El teléfono manda ISO con huso. Sin convertir, una venta de
    las 15:40 de Rosario se guardaba como las 15:40 de Berlín y aparecía cinco
    horas corrida en el historial.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


@router.post("/sales/sync", response_model=StandSyncResult)
def sync_sales(
    lote: List[StandSaleCreate],
    origen: str = Query("cola", pattern="^(cola|importada)$"),
    db: Session = Depends(get_db),
):
    """Carga ventas que YA OCURRIERON: la cola del teléfono y el archivo importado.

    Es otro endpoint y no un parámetro de `create_sale` por una diferencia que
    no es de forma, es de significado:

        POST /sales       → "cobrá esto"    · puede decir que no
        POST /sales/sync  → "esto se cobró" · solo puede acusar recibo

    **Acá el stock NO se valida.** La plata ya se cobró en la mano: rechazar
    la venta no devuelve la mercadería, solo borra el registro de algo que
    pasó. Si el stock queda negativo, queda negativo — y eso no significa que
    se vendió de más, significa que el inventario cargado no coincidía con lo
    que había en la caja. Es información, y la pantalla de Stock la muestra.

    Idempotente por `client_uuid`: el mismo lote se puede mandar las veces que
    haga falta. Es lo que permite exportar un archivo Y que además el teléfono
    sincronice solo cuando recupere señal, sin duplicar la caja.
    """
    resultado = {"creadas": 0, "repetidas": 0, "rechazadas": []}

    for data in lote:
        uuid = data.client_uuid
        if not uuid:
            resultado["rechazadas"].append({"client_uuid": None, "motivo": "Venta sin identificador"})
            continue

        if db.query(StandSale.id).filter(StandSale.client_uuid == uuid).first():
            resultado["repetidas"] += 1
            continue

        ids = [i.product_id for i in data.items]
        productos = {p.id: p for p in db.query(StandProduct).filter(StandProduct.id.in_(ids)).all()}
        if any(i not in productos for i in ids):
            resultado["rechazadas"].append({"client_uuid": uuid, "motivo": "Tiene un producto que ya no existe"})
            continue

        try:
            venta = StandSale(
                payment_method=data.payment_method,
                total=Decimal("0"),
                sold_by_volunteer_id=data.sold_by_volunteer_id,
                notes=data.notes,
                customer_name=data.customer_name,
                customer_email=data.customer_email,
                client_uuid=uuid,
                origen=origen,
            )
            # La fecha REAL de la venta, no la de la sincronización. Sin esto
            # una feria del sábado aparecía fechada el lunes.
            if data.occurred_at:
                venta.created_at = _a_hora_servidor(data.occurred_at)
            db.add(venta)
            db.flush()

            # El precio sale igual de la base y no del archivo: si viniera de
            # afuera, cualquiera podría editar el JSON antes de importarlo.
            total = Decimal("0")
            pedido: dict = {}
            for item in data.items:
                prod = productos[item.product_id]
                precio = Decimal(str(prod.unit_price))
                total += precio * item.quantity
                db.add(StandSaleItem(sale_id=venta.id, product_id=prod.id,
                                     quantity=item.quantity, unit_price=precio))
                if prod.inventory_item_id:
                    pedido[prod.inventory_item_id] = pedido.get(prod.inventory_item_id, 0) + item.quantity

            venta.total = total

            if pedido:
                for inv in db.query(Inventario).filter(Inventario.id.in_(list(pedido.keys()))).all():
                    # Puede quedar negativo, y está bien. Ver el docstring.
                    inv.quantity -= pedido[inv.id]

            db.commit()
            resultado["creadas"] += 1
        except Exception:
            db.rollback()
            log_error("Error al sincronizar una venta", module="stand",
                      action="sync_sale", meta={"client_uuid": uuid}, exc_info=True)
            resultado["rechazadas"].append({"client_uuid": uuid, "motivo": "No se pudo cargar"})

    log_info("Ventas sincronizadas", module="stand", action="sync_sales",
             meta={k: (v if k != "rechazadas" else len(v)) for k, v in resultado.items()})
    return resultado


def _ventas_del_rango(
    db: Session,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    incluir_anuladas: bool = True,
    limit: Optional[int] = None,
) -> List[StandSale]:
    """Las ventas de un período, de la más nueva a la más vieja.

    `hasta` se compara contra el día SIGUIENTE a medianoche: `created_at` es un
    TIMESTAMP, así que con `<= hasta` una venta de las 15:40 del último día
    quedaría afuera y nadie entendería por qué falta.
    """
    q = db.query(StandSale)
    if not incluir_anuladas:
        q = q.filter(StandSale.is_void == 0)
    if desde:
        q = q.filter(StandSale.created_at >= ar_date_to_server(desde))
    if hasta:
        q = q.filter(StandSale.created_at < ar_date_to_server(hasta, dia_siguiente=True))
    q = q.order_by(StandSale.created_at.desc(), StandSale.id.desc())
    return q.limit(limit).all() if limit else q.all()


@router.get("/sales", response_model=List[StandSaleOut])
def list_sales(
    limit: int = Query(50, le=5000),
    incluir_anuladas: bool = Query(True),
    # Con fechas, el `limit` deja de mandar: el período es el recorte. Sin
    # esto el historial mostraba siempre las últimas 50 y, pasadas esas,
    # escondía las viejas sin avisar.
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    hay_rango = bool(desde or hasta)
    ventas = _ventas_del_rango(
        db, desde, hasta, incluir_anuladas, limit=None if hay_rango else limit
    )
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


# -- Informe descargable -----------------------------------------------

def _medio_legible(medio: str) -> str:
    return {"efectivo": "Efectivo", "transferencia": "Transferencia"}.get(medio, medio or "-")


def _datos_informe(db: Session, desde: Optional[date], hasta: Optional[date]) -> dict:
    """Junta todo lo que necesitan el PDF y el Excel. Una sola consulta, dos
    formatos: si cada uno armara lo suyo, tarde o temprano darian numeros
    distintos para el mismo periodo."""
    ventas = _ventas_del_rango(db, desde, hasta, incluir_anuladas=True)
    vivas = [v for v in ventas if not v.is_void]
    anuladas = len(ventas) - len(vivas)

    productos = {p.id: p for p in db.query(StandProduct).all()}

    total = sum((Decimal(str(v.total or 0)) for v in vivas), Decimal("0"))
    unidades = sum(r.quantity for v in vivas for r in v.items)

    por_producto: dict = {}
    for v in vivas:
        for r in v.items:
            p = productos.get(r.product_id)
            nombre = (p.inventory_item.name if p and p.inventory_item else (p.name if p else "Producto eliminado"))
            acc = por_producto.setdefault(nombre, {"unidades": 0, "monto": Decimal("0")})
            acc["unidades"] += r.quantity
            acc["monto"] += Decimal(str(r.unit_price or 0)) * r.quantity

    por_medio: dict = {}
    for v in vivas:
        clave = _medio_legible(v.payment_method)
        por_medio[clave] = por_medio.get(clave, Decimal("0")) + Decimal(str(v.total or 0))

    return {
        "ventas": ventas,
        "vivas": vivas,
        "anuladas": anuladas,
        "productos": productos,
        "total": total,
        "unidades": unidades,
        "ticket": (total / len(vivas)) if vivas else Decimal("0"),
        "por_producto": por_producto,
        "por_medio": por_medio,
    }


def _nombre_producto(d: dict, product_id: int) -> str:
    p = d["productos"].get(product_id)
    if not p:
        return "Producto eliminado"
    return p.inventory_item.name if p.inventory_item else p.name


@router.get("/informe")
def informe(
    formato: str = Query("pdf", pattern="^(pdf|xlsx)$"),
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Informe de ventas del puesto, en PDF o Excel.

    Devuelve el archivo en base64 dentro de un JSON. Es lo que encaja con el
    `api-client` del front, que habla JSON y nada mas. A esta escala el 33%
    que infla base64 es irrelevante; con decenas de miles de ventas habria
    que pasar a stream.
    """
    d = _datos_informe(db, desde, hasta)
    periodo = _texto_periodo(desde, hasta)
    sufijo = date.today().isoformat()

    if formato == "xlsx":
        contenido = _informe_xlsx(d, periodo)
        return {
            "filename": f"ventas-puesto-{sufijo}.xlsx",
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "base64": base64.b64encode(contenido).decode(),
        }

    contenido = _informe_pdf(d, _periodo_pdf(d, desde, hasta))
    return {
        "filename": f"ventas-puesto-{sufijo}.pdf",
        "mime": "application/pdf",
        "base64": base64.b64encode(contenido).decode(),
    }


def _periodo_pdf(d: dict, desde: Optional[date], hasta: Optional[date]) -> str:
    """El periodo, en corto y para el membrete.

    Sin rango elegido dice "Todo el historial" Y entre parentesis desde cuando
    hasta cuando hay datos. Las dos cosas: la primera explica por que no hay
    filtro, la segunda evita que alguien lea el total como si cubriera anios
    que todavia no existen.
    """
    if desde or hasta:
        return reportes.periodo_corto(desde, hasta)

    fechas = [_con_huso(v.created_at) for v in d["ventas"]]
    fechas = [f.date() for f in fechas if f]
    if not fechas:
        return "Todo el historial"
    return f"Todo el historial ({reportes.periodo_corto(min(fechas), max(fechas))})"


def _texto_periodo(desde: Optional[date], hasta: Optional[date]) -> str:
    if desde and hasta:
        return f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    if desde:
        return f"Desde el {desde.strftime('%d/%m/%Y')}"
    if hasta:
        return f"Hasta el {hasta.strftime('%d/%m/%Y')}"
    return "Todo el historial"


def _informe_pdf(d: dict, periodo: str) -> bytes:
    """El PDF de ventas, agrupado por jornada.

    El corte por dia no es decorativo: el puesto trabaja por jornadas (una
    feria, un sabado en el Monumento) y la pregunta que se hace despues es
    "cuanto hicimos ese dia". Con una lista corrida habia que sumar a mano.
    """
    grupos = []
    actual = None
    for v in d["ventas"]:
        cuando = _con_huso(v.created_at)
        dia = cuando.date() if cuando else None

        if actual is None or actual["dia"] != dia:
            actual = {"dia": dia, "filas": [], "anuladas": set(),
                      "vivas": 0, "muertas": 0, "total": Decimal("0")}
            grupos.append(actual)

        detalle = ", ".join(
            f"{r.quantity}x {_nombre_producto(d, r.product_id)}" for r in v.items
        ) or "-"
        if v.is_void:
            actual["anuladas"].add(len(actual["filas"]))
            actual["muertas"] += 1
        else:
            actual["vivas"] += 1
            actual["total"] += Decimal(str(v.total or 0))

        actual["filas"].append([
            cuando.strftime("%H:%M") if cuando else "-",
            detalle,
            _medio_legible(v.payment_method),
            reportes.pesos(v.total),
        ])

    def rotulo(g: dict) -> str:
        partes = [f"{g['vivas']} venta" + ("" if g["vivas"] == 1 else "s")]
        if g["muertas"]:
            partes.append(f"{g['muertas']} anulada" + ("" if g["muertas"] == 1 else "s"))
        return " + ".join(partes)

    # El precio unitario sale de dividir lo recaudado por las unidades. Si un
    # producto cambio de precio dentro del periodo es un promedio, y es lo
    # correcto: el informe tiene que cerrar con el total, no con la lista de
    # precios de hoy.
    ranking = [
        (nombre,
         v["unidades"],
         float(v["monto"]) / v["unidades"] if v["unidades"] else 0,
         float(v["monto"]))
        for nombre, v in d["por_producto"].items()
    ]

    derecha = [reportes.Apilada("Medio de pago",
                                [(k, float(v)) for k, v in d["por_medio"].items()])]
    if d["anuladas"]:
        derecha.append(reportes.Aviso(
            f"<b>{d['anuladas']} venta{'' if d['anuladas'] == 1 else 's'} anulada"
            f"{'' if d['anuladas'] == 1 else 's'}</b> no "
            f"{'esta' if d['anuladas'] == 1 else 'estan'} incluida"
            f"{'' if d['anuladas'] == 1 else 's'} en los totales. "
            f"Aparece{'' if d['anuladas'] == 1 else 'n'} tachada"
            f"{'' if d['anuladas'] == 1 else 's'} en el detalle."
        ))

    return reportes.pdf_informe(
        titulo="Informe de ventas",
        subtitulo=f"Puesto de venta \u00b7 {periodo}",
        kpis=[
            ("Recaudado", reportes.pesos(d["total"])),
            ("Ventas", str(len(d["vivas"]))),
            ("Unidades", str(d["unidades"])),
            ("Ticket promedio", reportes.pesos(d["ticket"])),
        ],
        izquierda=reportes.Ranking("Por producto", ranking),
        derecha=derecha,
        tabla_titulo="Detalle de ventas",
        tabla_columnas=["Hora", "Productos", "Medio", "Total"],
        tabla_anchos=[48, 276, 96, 84],
        grupos=[
            reportes.Grupo(
                titulo=reportes.dia_corto(g["dia"]) if g["dia"] else "Sin fecha",
                detalle=rotulo(g),
                total=reportes.pesos(g["total"]),
                filas=g["filas"],
                anuladas=g["anuladas"],
            )
            for g in grupos
        ],
        total_label=f"Total recaudado \u00b7 {len(d['vivas'])} venta"
                    + ("" if len(d["vivas"]) == 1 else "s"),
        total_valor=reportes.pesos(d["total"]),
        pie=f"Comunidad alma \u00b7 Informe de ventas \u00b7 Puesto de venta",
    )


def _informe_xlsx(d: dict, periodo: str) -> bytes:
    # Hoja 1: una fila por RENGLON vendido. Permite las dos lecturas desde
    # Excel: sumar la columna Subtotal da la caja; agrupar por Producto dice
    # que se vende. Una fila por venta no deja hacer lo segundo.
    detalle = []
    for v in d["ventas"]:
        cuando = _con_huso(v.created_at)
        for r in v.items:
            detalle.append([
                cuando.date() if cuando else None,
                cuando.time().replace(microsecond=0) if cuando else None,
                _nombre_producto(d, r.product_id),
                r.quantity,
                float(r.unit_price or 0),
                float(Decimal(str(r.unit_price or 0)) * r.quantity),
                _medio_legible(v.payment_method),
                "Si" if v.is_void else "No",
            ])

    resumen = [[k, v["unidades"], float(v["monto"])]
               for k, v in sorted(d["por_producto"].items(), key=lambda x: -x[1]["monto"])]

    medios = [[k, float(v)] for k, v in d["por_medio"].items()]

    return reportes.xlsx_informe([
        reportes.Hoja(
            "Ventas",
            [("Fecha", "fecha"), ("Hora", "hora"), ("Producto", "texto"),
             ("Cantidad", "numero"), ("Precio unitario", "moneda"),
             ("Subtotal", "moneda"), ("Medio de pago", "texto"), ("Anulada", "texto")],
            detalle,
        ),
        reportes.Hoja(
            "Resumen por producto",
            [("Producto", "texto"), ("Unidades", "numero"), ("Recaudado", "moneda")],
            resumen,
        ),
        reportes.Hoja(
            "Medio de pago",
            [("Medio", "texto"), ("Recaudado", "moneda")],
            medios,
        ),
    ], periodo)
