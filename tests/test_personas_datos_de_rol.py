"""
La ficha de una persona trae los datos de sus roles.

Una persona es una fila de `participant_profiles`, pero sus roles viven en
otras dos tablas: la ficha de voluntario/a en `voluntarios` y la cuenta de login
en `participants`. Hasta ahora el listado de Personas solo resolvía `is_admin`;
todo lo demás había que ir a buscarlo al módulo de Voluntarios.

Eso es lo que obligaba a tener tres pantallas para mirar a una sola persona.
Ahora el payload trae también el estado del voluntariado, las especialidades, la
fecha de alta, y si la cuenta de participante sirve o no.

── Lo que más importa probar ──────────────────────────────────────────────
Que se resuelva con un número FIJO de consultas. La lista de personas es larga
y una consulta por fila la vuelve inusable apenas crece. El último test cuenta
las consultas de verdad, con un contador enganchado al motor.
"""
from datetime import date

import pytest
from sqlalchemy import event

from app.models.participant import (
    Participant,
    ParticipantProfile,
    ParticipantProgramEnrollment,
)
from app.models.voluntario import Voluntario


PIN_FALSO = "$2b$12$abcdefghijklmnopqrstuv"


@pytest.fixture()
def voluntaria(db):
    """Una persona con ficha de voluntaria activa, admin y con especialidades."""
    v = Voluntario(
        name="Dolores", last_name="Ibarra", email="dolores@ejemplo.com",
        registration_date=date(2024, 5, 20), status="activo", is_admin=True,
        specialties=["Acompañamiento", "Talleres"], email_verified=True,
    )
    db.add(v)
    db.flush()
    p = ParticipantProfile(
        name="Dolores", last_name="Ibarra", email="dolores@ejemplo.com",
        is_volunteer=True, volunteer_id=v.id, source="voluntario",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def traer(client, persona_id):
    r = client.get(f"/personas/{persona_id}")
    assert r.status_code == 200
    return r.json()


# ── El rol voluntario ──────────────────────────────────────────────────────

def test_trae_el_estado_del_voluntariado(client, voluntaria):
    assert traer(client, voluntaria.id)["volunteer_status"] == "activo"


def test_trae_las_especialidades(client, voluntaria):
    """El dato que vivía solo en el módulo de Voluntarios."""
    assert traer(client, voluntaria.id)["specialties"] == ["Acompañamiento", "Talleres"]


def test_trae_desde_cuando_es_voluntaria(client, voluntaria):
    assert traer(client, voluntaria.id)["volunteer_since"] == "2024-05-20"


def test_sigue_trayendo_is_admin(client, voluntaria):
    """Lo único que el listado ya resolvía: que no se haya roto."""
    assert traer(client, voluntaria.id)["is_admin"] is True


def test_una_persona_sin_ficha_no_inventa_datos_de_voluntariado(client):
    r = client.post("/personas/", json={"name": "Sola", "email": "sola@ejemplo.com"})
    datos = traer(client, r.json()["id"])

    assert datos["volunteer_status"] is None
    assert datos["specialties"] is None
    assert datos["volunteer_since"] is None
    assert datos["is_admin"] is False


def test_un_voluntario_pendiente_se_ve_como_pendiente(client, db, crear_voluntario):
    v = crear_voluntario(status="pendiente", email="recien@ejemplo.com")
    p = ParticipantProfile(name="Recien", email="recien@ejemplo.com",
                           is_volunteer=True, volunteer_id=v.id)
    db.add(p)
    db.commit()

    assert traer(client, p.id)["volunteer_status"] == "pendiente"


# ── La cuenta de participante ──────────────────────────────────────────────

def test_distingue_una_cuenta_que_sirve_de_una_apagada(client, db):
    """`participant_id` solo dice que la cuenta existe. Una apagada es la de
    alguien que pasó a ser voluntario/a: la ficha tiene que poder decirlo."""
    part = Participant(email="ana@ejemplo.com", pin_hash=PIN_FALSO,
                       is_active=False, email_verified=True)
    db.add(part)
    db.flush()
    p = ParticipantProfile(participant_id=part.id, name="Ana", email="ana@ejemplo.com")
    db.add(p)
    db.commit()

    datos = traer(client, p.id)
    assert datos["participant_id"] is not None
    assert datos["account_active"] is False
    assert datos["account_verified"] is True


def test_marca_a_quien_se_registro_y_nunca_entro_al_mail(client, db):
    part = Participant(email="tibia@ejemplo.com", pin_hash=PIN_FALSO,
                       is_active=True, email_verified=False)
    db.add(part)
    db.flush()
    db.add(ParticipantProfile(participant_id=part.id, name="Tibia",
                              email="tibia@ejemplo.com"))
    db.commit()
    p = db.query(ParticipantProfile).filter(
        ParticipantProfile.email == "tibia@ejemplo.com").first()

    datos = traer(client, p.id)
    assert datos["account_active"] is True
    assert datos["account_verified"] is False


def test_una_persona_sin_cuenta_no_inventa_nada(client, voluntaria):
    datos = traer(client, voluntaria.id)

    assert datos["participant_id"] is None
    assert datos["account_active"] is None
    assert datos["account_verified"] is None
    assert datos["enrollments_count"] == 0


def test_cuenta_las_inscripciones(client, db):
    part = Participant(email="ins@ejemplo.com", pin_hash=PIN_FALSO, is_active=True)
    db.add(part)
    db.flush()
    db.add(ParticipantProfile(participant_id=part.id, name="Ins", email="ins@ejemplo.com"))
    for i in (1, 2, 3):
        db.add(ParticipantProgramEnrollment(
            participant_id=part.id, type="taller", item_id=i))
    db.commit()
    p = db.query(ParticipantProfile).filter(
        ParticipantProfile.email == "ins@ejemplo.com").first()

    assert traer(client, p.id)["enrollments_count"] == 3


# ── El listado devuelve lo mismo que la ficha ──────────────────────────────

def test_el_listado_trae_los_mismos_datos_que_la_ficha(client, voluntaria):
    """Traer una persona sola no puede devolver menos que traerla en la lista."""
    de_la_lista = next(
        p for p in client.get("/personas/").json() if p["id"] == voluntaria.id)
    de_la_ficha = traer(client, voluntaria.id)

    for campo in ("volunteer_status", "specialties", "volunteer_since",
                  "is_admin", "account_active", "enrollments_count"):
        assert de_la_lista[campo] == de_la_ficha[campo], f"difieren en {campo}"


# ── Y la garantía que importa: nada de N+1 ─────────────────────────────────

def test_el_listado_no_hace_una_consulta_por_persona(client, db):
    """20 personas no pueden costar 20 consultas.

    Se cuentan las consultas reales contra el motor. El número exacto no
    importa y va a cambiar si se agrega algo; lo que se fija es que NO crezca
    con la cantidad de filas, que es la diferencia entre una lista que abre y
    una que se cuelga cuando la base crece.
    """
    for i in range(20):
        v = Voluntario(
            name=f"Vol{i}", last_name="Prueba", email=f"v{i}@ejemplo.com",
            registration_date=date(2026, 1, 1), status="activo",
            specialties=["Acompañamiento"], email_verified=True,
        )
        db.add(v)
        db.flush()
        part = Participant(email=f"v{i}@ejemplo.com", pin_hash=PIN_FALSO, is_active=False)
        db.add(part)
        db.flush()
        db.add(ParticipantProfile(
            name=f"Vol{i}", last_name="Prueba", email=f"v{i}@ejemplo.com",
            is_volunteer=True, volunteer_id=v.id, participant_id=part.id,
        ))
        db.add(ParticipantProgramEnrollment(
            participant_id=part.id, type="taller", item_id=1))
    db.commit()

    consultas = []
    motor = db.get_bind()

    def contar(conn, cursor, sentencia, *a, **kw):
        if sentencia.lstrip().upper().startswith("SELECT"):
            consultas.append(sentencia)

    event.listen(motor, "before_cursor_execute", contar)
    try:
        r = client.get("/personas/")
    finally:
        event.remove(motor, "before_cursor_execute", contar)

    assert r.status_code == 200
    assert len(r.json()) == 20
    # 1 por la lista + 3 por los roles. Con holgura por si se suma alguna.
    assert len(consultas) <= 8, (
        f"{len(consultas)} consultas para 20 personas — se está yendo a N+1:\n"
        + "\n".join(consultas)
    )


# ── Editar el voluntariado desde Base de datos ─────────────────────────────
#
# `specialties` entra por el PUT de personas aunque no sea una columna del
# perfil: es lo que permite editar a alguien en un solo formulario en vez de
# mandarlo al módulo de Voluntarios por un campo.

def test_se_pueden_editar_las_especialidades_desde_personas(client, db, voluntaria):
    r = client.put(f"/personas/{voluntaria.id}", json={"specialties": ["Grupos"]})

    assert r.status_code == 200
    assert r.json()["specialties"] == ["Grupos"]
    vol = db.query(Voluntario).filter(Voluntario.id == voluntaria.volunteer_id).first()
    db.refresh(vol)
    assert vol.specialties == ["Grupos"]


def test_se_pueden_vaciar_las_especialidades(client, db, voluntaria):
    """Vaciarlas es una edición válida, no un 'no mandaron nada'."""
    r = client.put(f"/personas/{voluntaria.id}", json={"specialties": []})

    assert r.status_code == 200
    vol = db.query(Voluntario).filter(Voluntario.id == voluntaria.volunteer_id).first()
    db.refresh(vol)
    assert vol.specialties == []


def test_editar_otra_cosa_no_toca_las_especialidades(client, db, voluntaria):
    client.put(f"/personas/{voluntaria.id}", json={"city": "Rosario"})

    vol = db.query(Voluntario).filter(Voluntario.id == voluntaria.volunteer_id).first()
    db.refresh(vol)
    assert vol.specialties == ["Acompañamiento", "Talleres"]


def test_mandar_especialidades_a_quien_no_es_voluntario_no_rompe(client):
    r = client.post("/personas/", json={"name": "Sola", "email": "sola2@ejemplo.com"})
    persona_id = r.json()["id"]

    r = client.put(f"/personas/{persona_id}", json={"specialties": ["Lo que sea"]})

    assert r.status_code == 200
    assert r.json()["specialties"] is None


def test_el_put_devuelve_la_persona_con_sus_datos_de_rol(client, voluntaria):
    """Después de guardar, la pantalla se queda con lo que devuelve el PUT: si
    viniera sin los datos del rol, las secciones se vaciarían solas."""
    datos = client.put(f"/personas/{voluntaria.id}", json={"city": "Rosario"}).json()

    assert datos["volunteer_status"] == "activo"
    assert datos["is_admin"] is True
    assert datos["volunteer_since"] == "2024-05-20"
