from typing import Optional

from pydantic import BaseModel


class Destinatario(BaseModel):
    """A quién le llegó. `nombre` puede venir vacío.

    Un email de `email_logs` es solo una dirección: si esa dirección no
    corresponde a nadie cargado —alguien que se dio de baja, una casilla
    escrita a mano— no hay nombre que poner, y se muestra el email pelado.
    Inventar un "Desconocido" sería peor: parecería un error del sistema
    cuando en realidad el envío salió bien.
    """

    nombre: Optional[str] = None
    email: Optional[str] = None
    tipo: Optional[str] = None       # voluntario | participante | None


class Aviso(BaseModel):
    """Una cosa que el sistema le mandó a una persona.

    Unifica dos tablas que no se parecen: `email_logs` (un mail, con
    destinatarios múltiples y estado de entrega) y `notifications` (una
    campanita, de a una persona por fila). Lo que tienen en común es lo único
    que esta pantalla necesita: cuándo, a quién, por qué y si salió.
    """

    id: str                          # "email:12" / "campanita:88" — únicos entre las dos fuentes
    canal: str                       # email | campanita
    fecha: Optional[str] = None
    tarea: Optional[str] = None      # template del mail o `kind` de la notificación
    titulo: Optional[str] = None     # asunto del mail / título de la campanita
    destinatario: Destinatario
    estado: str                      # enviado | fallido | leido | sin_leer
    error: Optional[str] = None
    automatico: bool = False         # lo disparó el cron, no una persona
