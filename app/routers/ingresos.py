"""Ingresos: toda la plata que entra, en un solo lugar.

No guarda nada. Es una vista de lectura sobre lo que ya registran los otros
módulos, y a propósito no tiene endpoint de alta: si se pudiera cargar un
ingreso desde acá habría dos formularios creando la misma fila, y con el
tiempo se separan. Para cargar, cada módulo.

Dos fuentes, que no se pueden unificar en una tabla:

* `person_payments` — pagos a nombre de una PERSONA (capacitaciones hoy; cuota
  de socio y donaciones el día que existan). `person_id` es NOT NULL.
* `stand_sales`     — ventas del puesto, que no tienen persona. Meterlas en la
  tabla de arriba obligaría a inventar a alguien por cada mate vendido.

Así que se suman al leer, cada una como un origen distinto — que además es lo
que se quiere ver.

Una advertencia que el resumen dice en voz alta: el total es *lo registrado*,
no *lo recaudado*. Si entró una donación y nadie la cargó, acá no está.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from decimal import Decimal
from datetime import date, datetime

from app.database import get_db
from app.models.access import PersonPayment
from app.models.stand import StandSale
from app.utils.logger import log_error

router = APIRouter()

# Cómo se llama cada origen en pantalla. `concept_type` es la columna con la
# que `person_payments` se segmenta sola; el stand es el único que hay que
# sumar aparte.
ETIQUETAS = {
    "capacitacion": "Academia",
    "cuota": "Cuota de socio",
    "donacion": "Donaciones",
    "stand": "Puesto de venta",
}


def _etiqueta(clave: str) -> str:
    return ETIQUETAS.get(clave, (clave or "Otros").replace("_", " ").capitalize())


def _medio(valor: Optional[str]) -> str:
    """Normaliza el medio de pago.

    La lista cerrada (`campos-pago.tsx`) guarda 'mercadopago' en minúscula,
    pero hay filas más viejas cargadas como 'MercadoPago' con texto libre.
    Sin esto, la misma plata aparece en dos renglones distintos.
    """
    v = (valor or "").strip().lower().replace(" ", "")
    if not v:
        return "sin especificar"
    if "mercado" in v:
        return "mercadopago"
    return v


@router.get("/resumen")
def resumen(year: Optional[int] = Query(None), db: Session = Depends(get_db)):
    try:
        # ── Años con movimiento, para los chips ──────────────────────────
        # El año se saca en Python y no con `YEAR()` de SQL: esa función es de
        # MySQL y no existe en SQLite, que es contra lo que corren los tests.
        # Se traen solo las fechas distintas, que es una columna y poco más.
        anios = {
            d.year
            for (d,) in db.query(PersonPayment.paid_at)
            .filter(PersonPayment.paid_at.isnot(None)).distinct().all()
        } | {
            d.year
            for (d,) in db.query(StandSale.created_at)
            .filter(StandSale.is_void == 0, StandSale.created_at.isnot(None))
            .distinct().all()
        }

        pagos = db.query(PersonPayment)
        ventas = db.query(StandSale).filter(StandSale.is_void == 0)
        if year:
            # Por rango y no por `YEAR(campo) = X`: además de ser SQL estándar,
            # una función sobre la columna impide usar el índice.
            pagos = pagos.filter(
                PersonPayment.paid_at >= date(year, 1, 1),
                PersonPayment.paid_at <= date(year, 12, 31),
            )
            ventas = ventas.filter(
                StandSale.created_at >= datetime(year, 1, 1),
                StandSale.created_at < datetime(year + 1, 1, 1),
            )

        pagos, ventas = pagos.all(), ventas.all()

        # ── Por origen ───────────────────────────────────────────────────
        por_origen: dict = {}
        for p in pagos:
            clave = p.concept_type or "otros"
            fila = por_origen.setdefault(clave, {"total": Decimal("0"), "operaciones": 0})
            fila["total"] += Decimal(str(p.amount or 0))
            fila["operaciones"] += 1
        if ventas:
            por_origen["stand"] = {
                "total": sum((Decimal(str(v.total or 0)) for v in ventas), Decimal("0")),
                "operaciones": len(ventas),
            }

        # ── Por medio de pago ────────────────────────────────────────────
        por_medio: dict = {}
        for p in pagos:
            por_medio[_medio(p.method)] = por_medio.get(_medio(p.method), Decimal("0")) + Decimal(str(p.amount or 0))
        for v in ventas:
            clave = _medio(v.payment_method)
            por_medio[clave] = por_medio.get(clave, Decimal("0")) + Decimal(str(v.total or 0))

        # ── Por mes ──────────────────────────────────────────────────────
        por_mes: dict = {m: Decimal("0") for m in range(1, 13)}
        for p in pagos:
            if p.paid_at:
                por_mes[p.paid_at.month] += Decimal(str(p.amount or 0))
        for v in ventas:
            if v.created_at:
                por_mes[v.created_at.month] += Decimal(str(v.total or 0))

        # ── Lo que no entra en ningún año ────────────────────────────────
        # `paid_at` es nullable y el filtro por año lo deja afuera SIEMPRE. Es
        # plata que está en la base y no se ve en ninguna pantalla, así que el
        # resumen la nombra en vez de tragársela.
        sin_fecha_q = db.query(PersonPayment).filter(PersonPayment.paid_at.is_(None)).all()

        total = sum((f["total"] for f in por_origen.values()), Decimal("0"))

        return {
            "year": year,
            "anios": sorted(anios, reverse=True),
            "total": total,
            "operaciones": sum(f["operaciones"] for f in por_origen.values()),
            "por_origen": sorted(
                (
                    {"key": k, "label": _etiqueta(k), "total": f["total"], "operaciones": f["operaciones"]}
                    for k, f in por_origen.items()
                ),
                key=lambda x: x["total"],
                reverse=True,
            ),
            "por_medio": sorted(
                ({"key": k, "total": t} for k, t in por_medio.items()),
                key=lambda x: x["total"],
                reverse=True,
            ),
            "por_mes": [{"month": m, "total": por_mes[m]} for m in range(1, 13)],
            "sin_fecha": {
                "cantidad": len(sin_fecha_q),
                "total": sum((Decimal(str(p.amount or 0)) for p in sin_fecha_q), Decimal("0")),
            },
        }
    except Exception:
        log_error("Error al armar el resumen de ingresos", module="ingresos",
                  action="resumen", meta={"year": year}, exc_info=True)
        raise
