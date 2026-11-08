"""Mantiene iguales los datos de contacto de una persona y su ficha de voluntario.

`participant_profiles` (la persona, registro maestro) y `voluntarios` (su ficha
de voluntario/a) guardan los mismos cuatro datos de contacto, cada una en su
columna. No es una duplicación que podamos sacar: hay ~20 FKs contra
`voluntarios.id` y 9 contra `participant_profiles.id`, y un montón de código
—incluido el cron de cumpleaños— lee uno u otro. Mover la propiedad de los
campos sería reescribir todas esas lecturas.

Lo que sí podemos es garantizar que las dos copias digan lo mismo. Las ALTAS ya
lo hacían (`_sync_persona_espejo`, y la copia inline de `enroll-from-db`): lo que
faltaba era la EDICIÓN. Editar a alguien en Voluntarios no avisaba a su persona,
ni al revés, así que las dos fichas se iban separando con cada cambio. Eso es lo
que se arregla acá.

── Por qué solo los campos que vinieron en el request ──────────────────────
La tentación es pisar los cuatro campos de un lado con los del otro. Rompe
datos: hay personas cargadas del Excel con teléfono que después se habilitaron
como voluntarias sin cargárselo, así que la persona tiene teléfono y el
voluntario no. Pisar todo en cada guardado le borraría el teléfono a esa
persona la primera vez que un admin le cambiara, por ejemplo, el estado.

Por eso se propaga el subconjunto que el PUT realmente mandó (el
`exclude_unset` del router): si editaste la fecha de nacimiento, viaja la fecha
de nacimiento y nada más. Un valor en `None` también viaja — borrar un dato es
una edición como cualquier otra.

── Por qué el email no viaja ───────────────────────────────────────────────
Es la credencial de login y tiene UNIQUE en las dos tablas. Propagarlo podría
chocar con otra fila y hacer fallar un guardado que no tenía nada que ver, y
cambiárselo a alguien por reflejo lo deja afuera de la plataforma. Se edita
donde se edita hoy, con sus propias validaciones (el 409 por duplicado).
"""

from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.participant import ParticipantProfile as ProfileModel
from app.models.voluntario import Voluntario as VoluntarioModel

# Los datos de contacto que viven en las dos tablas. `email` queda afuera a
# propósito (ver el encabezado). `age` tampoco está: quedó dormida y la edad se
# calcula desde `birth_date`.
CAMPOS_COMPARTIDOS = ("name", "last_name", "phone", "birth_date")


def _a_propagar(campos: Iterable[str]) -> list[str]:
    """Qué de lo que vino en el request es un dato compartido."""
    pedidos = set(campos)
    return [c for c in CAMPOS_COMPARTIDOS if c in pedidos]


def persona_de_voluntario(db: Session, volunteer_id: int) -> Optional[ProfileModel]:
    """La persona de un voluntario, buscada por el vínculo y no por el email.

    `volunteer_id` es el vínculo autoritativo (UNIQUE, uno a uno). Buscar por
    email —como hacen las altas— falla justo acá: si alguien le cambió el mail
    al voluntario, el email ya no coincide y la búsqueda devolvería None (o,
    peor, otra persona que tenga ese mail).
    """
    return (
        db.query(ProfileModel)
        .filter(ProfileModel.volunteer_id == volunteer_id)
        .first()
    )


def propagar_a_persona(db: Session, v: VoluntarioModel, campos: Iterable[str]) -> Optional[ProfileModel]:
    """Copia a la persona los datos compartidos que se acaban de editar en el voluntario.

    Devuelve la persona tocada, o None si no hay nada que hacer. NO commitea: lo
    hace el router, en la misma transacción que el guardado que lo disparó. Si
    algo falla, no queda una mitad escrita.
    """
    nombres = _a_propagar(campos)
    if not nombres:
        return None

    profile = persona_de_voluntario(db, v.id)
    if profile is None:
        # Un voluntario sin persona espejo es normal mientras está 'pendiente':
        # entra al directorio maestro recién al ser aprobado. No se crea acá
        # para no meter en la base de personas a alguien que todavía no fue
        # aprobado por nadie.
        return None

    for campo in nombres:
        setattr(profile, campo, getattr(v, campo))
    return profile


def propagar_a_voluntario(db: Session, p: ProfileModel, campos: Iterable[str]) -> Optional[VoluntarioModel]:
    """La inversa: de la persona a su ficha de voluntario, si tiene una.

    Mismas reglas que `propagar_a_persona`: solo los campos editados, sin email,
    sin commit.
    """
    nombres = _a_propagar(campos)
    if not nombres or p.volunteer_id is None:
        return None

    v = db.query(VoluntarioModel).filter(VoluntarioModel.id == p.volunteer_id).first()
    if v is None:
        return None

    for campo in nombres:
        setattr(v, campo, getattr(p, campo))
    return v


# ── Exclusión mutua de roles ───────────────────────────────────────────────
#
# Nadie puede ser voluntario/a y participante a la vez. No es un capricho: son
# dos logins con dos PINs sobre la misma persona, con permisos distintos, y la
# plataforma resuelve el rol por email. Con las dos cuentas vivas, cuál de los
# dos roles te toca depende de por qué puerta entraste.
#
# `enroll-from-db` ya lo respetaba (desactiva el login de participante al
# habilitar a alguien como voluntario/a). Las otras dos puertas que crean una
# ficha de voluntario —el ABM del admin y el auto-registro— no lo hacían.


def participante_activo_con_email(db: Session, email: Optional[str]):
    """La cuenta de participante activa con ese email, si hay alguna.

    Una cuenta `is_active=False` NO cuenta: es alguien a quien ya convirtieron
    en voluntario/a y cuyo login quedó guardado sin borrar, así que no es un
    choque sino el resultado de una conversión anterior.
    """
    from app.models.participant import Participant  # import local: evita el ciclo

    email = (email or "").strip().lower()
    if not email:
        return None
    return (
        db.query(Participant)
        .filter(Participant.email == email, Participant.is_active.is_(True))
        .first()
    )


def desactivar_login_participante(db: Session, profile: Optional[ProfileModel]):
    """Apaga el login de participante de una persona que pasa a ser voluntaria.

    DESACTIVA, no borra: la cuenta guarda inscripciones, pagos y progreso de
    capacitaciones, y acá nada se borra solo. Si algún día se le quita el rol de
    voluntaria, `revert-volunteer` la vuelve a prender con el PIN de siempre.

    Devuelve la cuenta apagada, o None si no había ninguna. No commitea.
    """
    from app.models.participant import Participant  # import local: evita el ciclo

    if profile is None or profile.participant_id is None:
        return None

    part = db.query(Participant).filter(Participant.id == profile.participant_id).first()
    if part is None or not part.is_active:
        return None

    part.is_active = False
    return part


def rellenar_huecos(destino, origen) -> list[str]:
    """Copia al destino los datos compartidos que tenga en blanco. Devuelve cuáles.

    Las altas ya rellenaban los huecos de la PERSONA con los datos del
    voluntario. Al revés no: habilitar como voluntaria a alguien que ya estaba
    cargada en la base le creaba una ficha sin el teléfono ni la fecha de
    nacimiento que la persona ya tenía.

    Eso le pegaba justo al cron de cumpleaños, que lee `voluntarios.birth_date`:
    la fecha estaba en el sistema, en la ficha de la persona, pero la columna
    que el cron mira quedaba NULL y a esa persona no la saludaba nadie.

    Rellena, no pisa: si el destino ya tiene algo cargado, queda como está. Es
    la operación del ALTA; para la edición está `propagar_*`, que sí pisa pero
    solo los campos que el request mandó.
    """
    llenados = []
    for campo in CAMPOS_COMPARTIDOS:
        if getattr(destino, campo, None) in (None, ""):
            valor = getattr(origen, campo, None)
            if valor not in (None, ""):
                setattr(destino, campo, valor)
                llenados.append(campo)
    return llenados
