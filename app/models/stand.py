from sqlalchemy import Column, Integer, String, DECIMAL, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database import Base


class StandProduct(Base):
    """La góndola: qué ítem del inventario está a la venta y a qué precio.

    Ya no es un catálogo propio. Durante la jornada lo fue —se armó así por
    falta de tiempo— y el costo era que la misma mercadería vivía en dos
    lugares: vendías 13 mates acá y el inventario seguía diciendo que estaban
    todos. Ahora la madre es `inventario` y esta tabla responde otra pregunta:

        inventario     → qué tenemos  (cantidad, mínimo, proveedor, responsable)
        stand_products → qué ofrecemos (precio de venta, orden, activo)

    No hay un `for_sale` en `inventario` a propósito: "está a la venta" es
    tener fila activa acá. Dos columnas contestando lo mismo terminan
    contestando distinto.
    """

    __tablename__ = "stand_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    # NULL solo para los productos anteriores a la unión (sql/25). Todo lo que
    # se crea desde acá nace enganchado.
    inventory_item_id = Column(
        Integer, ForeignKey("inventario.id", ondelete="RESTRICT"), nullable=True
    )
    unit_price = Column(DECIMAL(12, 2), nullable=False, default=0)
    # Vestigial para los productos enganchados: el stock sale de
    # `inventario.quantity`. Se conserva porque para los de la jornada guarda
    # con cuánto se arrancó, que es historia que no se recupera de otro lado.
    initial_stock = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())

    inventory_item = relationship("Inventario", lazy="joined")


class StandSale(Base):
    """Una venta: varios productos cobrados juntos con un solo medio de pago."""

    __tablename__ = "stand_sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_method = Column(String(20), nullable=False)  # efectivo | transferencia
    total = Column(DECIMAL(12, 2), nullable=False, default=0)
    # Sin FK: si un voluntario se da de baja, la venta tiene que sobrevivir.
    sold_by_volunteer_id = Column(Integer, nullable=True)
    notes = Column(String(255), nullable=True)
    # Datos opcionales de quien compra. No se usan para mandar nada todavía:
    # se guardan para decidir después. Texto suelto, sin FK a personas.
    customer_name = Column(String(120), nullable=True)
    customer_email = Column(String(160), nullable=True)
    # Baja lógica. Nada se borra solo, y menos la caja.
    is_void = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now())

    # ── Ventas que no nacieron contra el servidor ──────────────────────
    #
    # El puesto trabaja donde la señal es mala. Una venta se cobra igual, se
    # guarda en el teléfono y llega después: por reintento automático o por un
    # archivo que alguien importa.
    #
    # `client_uuid` lo genera el teléfono ANTES de mandar nada, y es lo que
    # hace que los dos caminos no se pisen. Tres casos reales:
    #   · el POST llega pero la respuesta se pierde → el teléfono reintenta
    #   · se exporta el archivo Y además el teléfono recupera señal
    #   · alguien importa dos veces el mismo archivo
    # En los tres, la segunda vez se reconoce y se ignora.
    #
    # NULL permitido: las ventas anteriores a esto no lo tienen, y en MySQL un
    # índice único convive con cuantos NULL hagan falta.
    client_uuid = Column(String(36), nullable=True, unique=True)

    # Por dónde entró: vivo | cola | importada. Cuando la caja no cierra,
    # saber por qué camino llegó una venta es la mitad de la respuesta.
    origen = Column(String(12), nullable=False, default="vivo")

    items = relationship("StandSaleItem", back_populates="sale",
                         cascade="all, delete-orphan", lazy="joined")


class StandSaleItem(Base):
    """Renglón de una venta, con el precio congelado al momento de cobrar."""

    __tablename__ = "stand_sale_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sale_id = Column(Integer, ForeignKey("stand_sales.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("stand_products.id", ondelete="RESTRICT"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(DECIMAL(12, 2), nullable=False, default=0)

    sale = relationship("StandSale", back_populates="items")
    product = relationship("StandProduct", lazy="joined")
