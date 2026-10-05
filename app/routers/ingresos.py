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
import base64
from decimal import Decimal
from datetime import date, datetime, time, timedelta

from app.database import get_db
from app.models.access import PersonPayment
from app.models.stand import StandSale
from app.models.participant import ParticipantProfile
from app.utils.timezone import ar_date_to_server
from app.utils.logger import log_error
from app.services import reportes

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


# -- Informe descargable -----------------------------------------------

def _movimientos(db: Session, desde: date, hasta: date) -> list[dict]:
    """Los ingresos del periodo, de las dos fuentes, en una sola lista.

    Se arma aca y no en `resumen` porque el informe necesita el DETALLE fila
    por fila, no los totales. Los dos leen las mismas tablas con el mismo
    criterio de fechas, asi que no pueden dar numeros distintos.
    """
    filas = []

    pagos = (
        db.query(PersonPayment)
        .filter(PersonPayment.paid_at >= desde, PersonPayment.paid_at <= hasta)
        .all()
    )
    personas = {}
    if pagos:
        ids = [p.person_id for p in pagos]
        personas = {
            x.id: f"{x.name or ''} {x.last_name or ''}".strip()
            for x in db.query(ParticipantProfile).filter(ParticipantProfile.id.in_(ids)).all()
        }

    for p in pagos:
        filas.append({
            "fecha": p.paid_at,
            "origen": _etiqueta(p.concept_type or "otros"),
            "concepto": p.concept_label or "-",
            "persona": personas.get(p.person_id) or "-",
            "medio": _medio(p.method),
            "monto": Decimal(str(p.amount or 0)),
        })

    ventas = (
        db.query(StandSale)
        .filter(
            StandSale.is_void == 0,
            StandSale.created_at >= ar_date_to_server(desde),
            StandSale.created_at < ar_date_to_server(hasta, dia_siguiente=True),
        )
        .all()
    )
    for v in ventas:
        filas.append({
            "fecha": v.created_at.date() if v.created_at else None,
            "origen": _etiqueta("stand"),
            "concepto": "Venta del puesto",
            "persona": v.customer_name or "-",
            "medio": _medio(v.payment_method),
            "monto": Decimal(str(v.total or 0)),
        })

    filas.sort(key=lambda f: (f["fecha"] or date.min), reverse=True)
    return filas


@router.get("/informe")
def informe(
    formato: str = Query("pdf", pattern="^(pdf|xlsx)$"),
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Informe de ingresos (Academia + puesto de venta) en PDF o Excel.

    Sin fechas toma el anio en curso: un informe de "todo el historial" de la
    plata rara vez es lo que alguien quiere, y en cambio equivocarse de rango
    y bajar diez anios si molesta.
    """
    hoy = date.today()
    desde = desde or date(hoy.year, 1, 1)
    hasta = hasta or hoy

    filas = _movimientos(db, desde, hasta)
    total = sum((f["monto"] for f in filas), Decimal("0"))

    por_origen: dict = {}
    por_medio: dict = {}
    for f in filas:
        por_origen[f["origen"]] = por_origen.get(f["origen"], Decimal("0")) + f["monto"]
        por_medio[f["medio"]] = por_medio.get(f["medio"], Decimal("0")) + f["monto"]

    periodo = f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}"
    sufijo = hoy.isoformat()

    if formato == "xlsx":
        contenido = reportes.xlsx_informe([
            reportes.Hoja(
                "Movimientos",
                [("Fecha", "fecha"), ("Origen", "texto"), ("Concepto", "texto"),
                 ("Persona", "texto"), ("Medio de pago", "texto"), ("Monto", "moneda")],
                [[f["fecha"], f["origen"], f["concepto"], f["persona"],
                  f["medio"].capitalize(), float(f["monto"])] for f in filas],
            ),
            reportes.Hoja(
                "Resumen por origen",
                [("Origen", "texto"), ("Monto", "moneda")],
                [[k, float(v)] for k, v in sorted(por_origen.items(), key=lambda x: -x[1])],
            ),
            reportes.Hoja(
                "Medio de pago",
                [("Medio", "texto"), ("Monto", "moneda")],
                [[k.capitalize(), float(v)] for k, v in sorted(por_medio.items(), key=lambda x: -x[1])],
            ),
        ], periodo)
        return {
            "filename": f"ingresos-{sufijo}.xlsx",
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "base64": base64.b64encode(contenido).decode(),
        }

    sin_fecha = db.query(PersonPayment).filter(PersonPayment.paid_at.is_(None)).count()
    nota = None
    if sin_fecha:
        nota = (f"<b>{sin_fecha} pago{'' if sin_fecha == 1 else 's'} "
                f"sin fecha</b> no "
                f"{'entra' if sin_fecha == 1 else 'entran'} en ningun periodo, "
                f"asi que no "
                f"{'esta sumado' if sin_fecha == 1 else 'estan sumados'} "
                "en este informe.")

    # El detalle, cortado por dia. `_movimientos` ya los devuelve del mas
    # nuevo al mas viejo, asi que alcanza con mirar cuando cambia la fecha.
    grupos = []
    actual = None
    for f in filas:
        if actual is None or actual["dia"] != f["fecha"]:
            actual = {"dia": f["fecha"], "filas": [], "total": Decimal("0")}
            grupos.append(actual)
        actual["total"] += f["monto"]
        actual["filas"].append([
            f["origen"],
            f["concepto"][:52],
            f["persona"][:26] or "\u2014",
            f["medio"].capitalize(),
            reportes.pesos(f["monto"]),
        ])

    promedio = (total / len(filas)) if filas else Decimal("0")

    contenido = reportes.pdf_informe(
        titulo="Informe de ingresos",
        subtitulo=f"Academia y puesto de venta \u00b7 {reportes.periodo_corto(desde, hasta)}",
        kpis=[
            ("Total ingresado", reportes.pesos(total)),
            ("Movimientos", str(len(filas))),
            ("Promedio por movimiento", reportes.pesos(promedio)),
        ],
        izquierda=reportes.Barras(
            "De donde viene",
            [(k, float(v)) for k, v in por_origen.items()],
        ),
        derecha=(
            [reportes.Apilada("Como entro",
                              [(k.capitalize(), float(v)) for k, v in por_medio.items()])]
            + ([reportes.Aviso(nota)] if nota else [])
        ),
        tabla_titulo="Detalle de movimientos",
        tabla_columnas=["Origen", "Concepto", "Persona", "Medio", "Monto"],
        tabla_anchos=[66, 186, 96, 76, 80],
        grupos=[
            reportes.Grupo(
                titulo=reportes.dia_corto(g["dia"]) if g["dia"] else "Sin fecha",
                detalle=f"{len(g['filas'])} movimiento"
                        + ("" if len(g["filas"]) == 1 else "s"),
                total=reportes.pesos(g["total"]),
                filas=g["filas"],
            )
            for g in grupos
        ],
        total_label="Total del periodo",
        total_valor=reportes.pesos(total),
        pie=f"Comunidad alma \u00b7 Informe de ingresos \u00b7 "
            f"{reportes.periodo_corto(desde, hasta)}",
    )
    return {
        "filename": f"ingresos-{sufijo}.pdf",
        "mime": "application/pdf",
        "base64": base64.b64encode(contenido).decode(),
    }
