"""
El ciclo de vida completo de una persona, de punta a punta.

Los otros dos archivos prueban cada pieza por separado: `test_espejo_persona`
que las dos copias de los datos de contacto no se separen, y
`test_roles_excluyentes` que nadie termine con los dos logins vivos. Este
recorre el camino real de alguien —se registra, compra, la hacen voluntaria, le
editan los datos de los dos lados, le quitan el rol— y después de CADA paso
verifica los mismos tres invariantes.

Por qué existe aparte: los bugs que aparecieron acá no estaban en ninguna
función, estaban en la costura entre dos. Una ficha de voluntario y una persona
se separaban porque el alta las sincronizaba y la edición no; alguien terminaba
con dos roles porque una puerta validaba el cruce y la otra no. Nada de eso lo
ve un test que mira un endpoint solo.

── Los invariantes ────────────────────────────────────────────────────────
  1. Una persona, un registro. No se duplica `participant_profiles`.
  2. Un rol a la vez. Nunca el login de participante activo y la ficha de
     voluntario/a vigente al mismo tiempo.
  3. Las dos copias dicen lo mismo. nombre, apellido, teléfono y fecha de
     nacimiento coinciden entre `voluntarios` y la persona.

El invariante 3 es el que le importa al cron de cumpleaños: lee
`voluntarios.birth_date`, así que si esa columna se separa de la que muestra
Base de datos, el cron saluda según un dato que la pantalla desmiente. Acá no se
corre el cron (su SQL es de MySQL y estos tests van sobre SQLite): se verifica
la columna que el cron lee, que es de donde salía el problema.
"""
from datetime import date

import pytest

from app.models.participant import Participant, ParticipantProfile
from app.models.voluntario import Voluntario


PIN_FALSO = "$2b$12$abcdefghijklmnopqrstuv"
CAMPOS = ("name", "last_name", "phone", "birth_date")


def verificar_invariantes(db, email: str) -> None:
    """Los tres invariantes, para la persona de ese email. Se llama en cada paso."""
    perfiles = db.query(ParticipantProfile).filter(
        ParticipantProfile.email == email).all()

    # 1. Una persona, un registro.
    assert len(perfiles) <= 1, f"{email} quedó duplicada en participant_profiles"
    if not perfiles:
        return
    prof = perfiles[0]
    db.refresh(prof)

    vol = None
    if prof.volunteer_id is not None:
        vol = db.query(Voluntario).filter(Voluntario.id == prof.volunteer_id).first()
        if vol is not None:
            db.refresh(vol)

    part = None
    if prof.participant_id is not None:
        part = db.query(Participant).filter(Participant.id == prof.participant_id).first()
        if part is not None:
            db.refresh(part)

    # 2. Un rol a la vez. Una ficha 'inactivo' no cuenta: es un rol retirado.
    voluntaria_vigente = vol is not None and vol.status != "inactivo"
    participante_vigente = part is not None and part.is_active
    assert not (voluntaria_vigente and participante_vigente), (
        f"{email} tiene los dos roles vivos: "
        f"voluntario status={vol.status!r}, login is_active={part.is_active!r}"
    )

    # 3. Las dos copias dicen lo mismo.
    if vol is not None:
        for campo in CAMPOS:
            assert getattr(prof, campo) == getattr(vol, campo), (
                f"{email}: '{campo}' difiere — "
                f"persona={getattr(prof, campo)!r} voluntario={getattr(vol, campo)!r}"
            )


@pytest.fixture()
def persona(client, db):
    """Devuelve un ayudante que lee el estado actual de nuestra persona."""
    def _leer():
        return db.query(ParticipantProfile).filter(
            ParticipantProfile.email == "ana@ejemplo.com").first()
    return _leer


def test_el_ciclo_de_vida_completo(client, db, persona):
    """Un recorrido entero, verificando los invariantes después de cada paso."""
    email = "ana@ejemplo.com"

    # ── 1. Se registra como participante por el formulario público ──────
    r = client.post("/register/participante", json={
        "email": email, "pin_hash": PIN_FALSO, "name": "Ana", "last_name": "Diaz",
    })
    assert r.status_code == 201
    verificar_invariantes(db, email)

    prof = persona()
    assert prof is not None, "el registro tiene que dejarle su ficha de persona"
    assert prof.participant_id is not None
    assert prof.volunteer_id is None

    # ── 2. El admin le completa los datos en Base de datos ──────────────
    r = client.put(f"/personas/{prof.id}", json={
        "phone": "341-5550000", "birth_date": "1962-03-14", "city": "Rosario",
    })
    assert r.status_code == 200
    verificar_invariantes(db, email)

    # ── 3. La habilitan como voluntaria (hereda el PIN) ─────────────────
    r = client.post("/voluntarios/enroll-from-db", json={
        "persona_id": prof.id, "name": "Ana", "last_name": "Diaz", "email": email,
    })
    assert r.status_code == 201
    vol_id = r.json()["id"]
    verificar_invariantes(db, email)

    db.refresh(prof)
    assert prof.volunteer_id == vol_id
    assert prof.is_volunteer is True
    # Entra con el mismo PIN, ahora como voluntaria.
    vol = db.query(Voluntario).filter(Voluntario.id == vol_id).first()
    assert vol.pin_hash == PIN_FALSO
    assert vol.status == "activo"
    # Y su login de participante quedó apagado, no borrado.
    part = db.query(Participant).filter(Participant.id == prof.participant_id).first()
    assert part.is_active is False
    assert part.pin_hash == PIN_FALSO

    # El dato que mira el cron de cumpleaños viajó con ella.
    assert vol.birth_date == date(1962, 3, 14)

    # ── 4. Le corrigen la fecha de nacimiento DESDE Voluntarios ─────────
    #     Este era el bug original: quedaba distinta en cada pantalla.
    r = client.put(f"/voluntarios/{vol_id}", json={"birth_date": "1962-03-15"})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    db.refresh(prof)
    assert prof.birth_date == date(1962, 3, 15), "la persona no se enteró del cambio"

    # ── 5. Le corrigen el teléfono DESDE Base de datos ─────────────────
    r = client.put(f"/personas/{prof.id}", json={"phone": "341-5559999"})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    db.refresh(vol)
    assert vol.phone == "341-5559999", "la ficha de voluntaria no se enteró"

    # ── 6. Le marcan una especialidad (campo que solo existe de un lado) ─
    r = client.put(f"/voluntarios/{vol_id}", json={"specialties": ["Acompañamiento"]})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    db.refresh(prof)
    assert prof.phone == "341-5559999", "un campo propio del rol pisó datos compartidos"
    assert prof.birth_date == date(1962, 3, 15)

    # ── 7. Le quitan el rol de voluntaria ──────────────────────────────
    r = client.post("/participants/revert-volunteer", json={"persona_id": prof.id})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    db.refresh(prof)
    assert prof.volunteer_id is None
    assert prof.is_volunteer is False
    # Vuelve a entrar como participante, con el PIN de siempre.
    db.refresh(part)
    assert part.is_active is True
    assert part.pin_hash == PIN_FALSO
    # La ficha de voluntaria no se borró: quedó inactiva.
    db.refresh(vol)
    assert vol.status == "inactivo"
    assert vol.specialties == ["Acompañamiento"], "se perdió un dato del rol"

    # ── 8. Y la vuelven a habilitar ────────────────────────────────────
    r = client.post("/voluntarios/enroll-from-db", json={
        "persona_id": prof.id, "name": "Ana", "last_name": "Diaz", "email": email,
    })
    assert r.status_code == 201
    verificar_invariantes(db, email)

    # Se reusó la misma fila, no se duplicó el email.
    assert db.query(Voluntario).filter(Voluntario.email == email).count() == 1
    assert db.query(ParticipantProfile).count() == 1


def test_el_camino_del_voluntario_de_cero(client, db):
    """La otra entrada: alguien que nunca fue participante."""
    email = "nadia@ejemplo.com"

    # ── 1. Se registra por el formulario público ────────────────────────
    r = client.post("/voluntarios/register", json={
        "name": "Nadia", "last_name": "Rossi", "email": email,
    })
    assert r.status_code == 201
    vol_id = r.json()["id"]
    verificar_invariantes(db, email)

    # Queda pendiente y todavía NO está en el directorio de personas: entra al
    # ser aprobada. Editarla mientras espera no la mete por la ventana.
    assert db.query(ParticipantProfile).count() == 0

    r = client.put(f"/voluntarios/{vol_id}", json={"birth_date": "1975-06-02"})
    assert r.status_code == 200
    assert db.query(ParticipantProfile).count() == 0
    verificar_invariantes(db, email)

    # ── 2. El admin la aprueba ──────────────────────────────────────────
    r = client.post(f"/voluntarios/{vol_id}/approve")
    assert r.status_code == 200
    verificar_invariantes(db, email)

    prof = db.query(ParticipantProfile).filter(
        ParticipantProfile.email == email).first()
    assert prof is not None, "al aprobarla tiene que entrar al directorio"
    assert prof.volunteer_id == vol_id
    # Y se lleva los datos que le cargaron mientras estaba pendiente.
    assert prof.birth_date == date(1975, 6, 2)

    # ── 3. De acá en adelante los dos lados se mantienen iguales ────────
    client.put(f"/voluntarios/{vol_id}", json={"phone": "341-5551234"})
    verificar_invariantes(db, email)

    client.put(f"/personas/{prof.id}", json={"last_name": "Rossi Vega"})
    verificar_invariantes(db, email)

    db.refresh(prof)
    vol = db.query(Voluntario).filter(Voluntario.id == vol_id).first()
    assert vol.last_name == "Rossi Vega"
    assert prof.phone == "341-5551234"


def test_la_persona_cargada_a_mano_que_nunca_entra_a_la_plataforma(client, db):
    """El caso más común de todos: alguien del Excel, sin login y sin rol."""
    email = "cecilia@ejemplo.com"

    r = client.post("/personas/", json={
        "name": "Cecilia", "last_name": "Cabral", "email": email,
        "phone": "341-5557777", "birth_date": "1948-11-30",
    })
    assert r.status_code == 201
    verificar_invariantes(db, email)

    prof = db.query(ParticipantProfile).filter(
        ParticipantProfile.email == email).first()
    assert prof.participant_id is None, "cargarla no le crea cuenta"
    assert prof.volunteer_id is None
    assert prof.is_volunteer is False

    # Se la puede editar sin que nada se rompa por no tener roles.
    r = client.put(f"/personas/{prof.id}", json={"birth_date": "1948-12-01"})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    # ── Y después se la invita a tener cuenta ───────────────────────────
    r = client.post("/participants/invite", json={"profile_id": prof.id})
    assert r.status_code == 200
    verificar_invariantes(db, email)

    db.refresh(prof)
    assert prof.participant_id is not None, "la invitación tiene que vincular el login"
    # Sin duplicarla: es la misma persona, no una nueva.
    assert db.query(ParticipantProfile).count() == 1
