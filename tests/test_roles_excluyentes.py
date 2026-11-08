"""
Nadie es voluntario/a y participante a la vez.

Son dos logins, con dos PINs, sobre la misma persona, y con permisos muy
distintos. La plataforma resuelve el rol por email, así que con las dos cuentas
vivas el rol que te toca depende de por qué puerta entraste: entrás por el login
de participante y sos read-only, entrás por el de voluntario/a y editás grupos.
Eso no es un rol ambiguo, es un agujero.

`enroll-from-db` ya respetaba la regla (apaga el login de participante al
habilitar a alguien). Las otras dos puertas que crean una ficha de voluntario no
lo hacían, cada una por un motivo distinto y con un arreglo distinto:

  · el AUTO-REGISTRO público rechaza con 409. Pasar a voluntario/a es una
    decisión del equipo, no algo que uno se dé a sí mismo en un formulario.
  · el ABM del admin CONVIERTE (apaga el login) en vez de rechazar, porque por
    esa misma ruta pasa la restauración de un backup completo y un 409 la
    cortaría por la mitad.

La cuenta se APAGA, nunca se borra: guarda inscripciones, pagos y progreso de
capacitaciones, y `revert-volunteer` la vuelve a prender con el PIN de siempre.
"""
from datetime import date

import pytest

from app.models.participant import Participant, ParticipantProfile
from app.models.voluntario import Voluntario


PIN_FALSO = "$2b$12$abcdefghijklmnopqrstuv"


@pytest.fixture()
def participante_registrado(client):
    """Alguien que se registró como participante por el formulario público."""
    r = client.post("/register/participante", json={
        "email": "ana@ejemplo.com", "pin_hash": PIN_FALSO,
        "name": "Ana", "last_name": "Diaz",
    })
    assert r.status_code == 201
    return r.json()


# ── El auto-registro público: se rechaza ───────────────────────────────────

def test_un_participante_no_puede_registrarse_como_voluntario(client, participante_registrado):
    """El agujero original: nadie lo frenaba y quedaba con los dos roles."""
    r = client.post("/voluntarios/register", json={
        "name": "Ana", "last_name": "Diaz", "email": "ana@ejemplo.com",
    })

    assert r.status_code == 409


def test_el_rechazo_le_dice_que_hacer(client, participante_registrado):
    """Un 409 que no explica el camino bueno deja a la persona trabada."""
    r = client.post("/voluntarios/register", json={
        "name": "Ana", "email": "ana@ejemplo.com",
    })

    detalle = r.json()["detail"]
    assert "participante" in detalle
    assert "habilite" in detalle


def test_el_rechazo_no_deja_ninguna_ficha_a_medias(client, db, participante_registrado):
    client.post("/voluntarios/register", json={"name": "Ana", "email": "ana@ejemplo.com"})

    assert db.query(Voluntario).count() == 0


def test_un_email_libre_se_registra_sin_problema(client):
    """Control: el chequeo nuevo no rompe el alta normal."""
    r = client.post("/voluntarios/register", json={
        "name": "Nadia", "last_name": "Rossi", "email": "nadia@ejemplo.com",
    })

    assert r.status_code == 201


def test_un_login_ya_apagado_no_bloquea(client, db):
    """Una cuenta `is_active=False` es el resto de una conversión anterior, no un
    choque: no puede dejar a la persona sin poder registrarse de nuevo."""
    db.add(Participant(email="vieja@ejemplo.com", pin_hash=PIN_FALSO, is_active=False))
    db.commit()

    r = client.post("/voluntarios/register", json={
        "name": "Vieja", "email": "vieja@ejemplo.com",
    })

    assert r.status_code == 201


# ── El ABM del admin: se convierte ─────────────────────────────────────────

def test_el_admin_crea_el_voluntario_y_le_apaga_el_login(client, db, participante_registrado):
    r = client.post("/voluntarios/", json={
        "name": "Ana", "last_name": "Diaz", "email": "ana@ejemplo.com",
        "registration_date": "2026-11-08", "status": "activo",
    })

    assert r.status_code == 201
    part = db.query(Participant).filter(Participant.email == "ana@ejemplo.com").first()
    db.refresh(part)
    assert part.is_active is False


def test_el_abm_no_rechaza_para_no_cortar_una_restauracion(client, participante_registrado):
    """Por esta ruta pasa el import de un backup completo. Un 409 lo partiría al
    medio y dejaría la base a mitad de camino."""
    r = client.post("/voluntarios/", json={
        "name": "Ana", "email": "ana@ejemplo.com",
        "registration_date": "2026-11-08", "status": "activo",
    })

    assert r.status_code == 201


def test_apagar_el_login_no_borra_nada(client, db, participante_registrado):
    """La cuenta guarda inscripciones y pagos. Se apaga, no se borra."""
    client.post("/voluntarios/", json={
        "name": "Ana", "email": "ana@ejemplo.com",
        "registration_date": "2026-11-08", "status": "activo",
    })

    part = db.query(Participant).filter(Participant.email == "ana@ejemplo.com").first()
    assert part is not None
    assert part.pin_hash == PIN_FALSO


def test_la_persona_queda_con_un_solo_registro_y_los_dos_vinculos(client, db, participante_registrado):
    """No se duplica la persona: se reusa la que ya estaba, por email."""
    client.post("/voluntarios/", json={
        "name": "Ana", "email": "ana@ejemplo.com",
        "registration_date": "2026-11-08", "status": "activo",
    })

    assert db.query(ParticipantProfile).count() == 1
    prof = db.query(ParticipantProfile).first()
    assert prof.volunteer_id is not None
    assert prof.is_volunteer is True


def test_el_login_apagado_no_puede_entrar(client, db, participante_registrado):
    """Lo que hace que apagarlo sirva: el frontend corta si `is_active` es False."""
    client.post("/voluntarios/", json={
        "name": "Ana", "email": "ana@ejemplo.com",
        "registration_date": "2026-11-08", "status": "activo",
    })

    r = client.get("/participants/auth/ana@ejemplo.com")

    assert r.status_code == 200
    assert r.json()["is_active"] is False


# ── Aprobar un pendiente ───────────────────────────────────────────────────

def test_aprobar_tambien_apaga_el_login_de_participante(client, db):
    """Las dos puertas que dejan a alguien como voluntario/a activo tienen que
    terminar en el mismo estado.

    El estado se arma a mano porque el registro ahora lo frena antes: esto cubre
    los casos que quedaron de antes del arreglo y cualquier camino futuro.
    """
    part = Participant(email="luis@ejemplo.com", pin_hash=PIN_FALSO, is_active=True)
    db.add(part)
    db.flush()
    db.add(ParticipantProfile(
        participant_id=part.id, name="Luis", last_name="Paz",
        email="luis@ejemplo.com", source="registro",
    ))
    vol = Voluntario(
        name="Luis", last_name="Paz", email="luis@ejemplo.com",
        registration_date=date(2026, 11, 8), status="pendiente", email_verified=True,
    )
    db.add(vol)
    db.commit()
    db.refresh(vol)

    r = client.post(f"/voluntarios/{vol.id}/approve")

    assert r.status_code == 200
    db.refresh(part)
    assert part.is_active is False


def test_aprobar_a_alguien_sin_login_de_participante_no_rompe(client, db, crear_voluntario):
    """El caso normal: la enorme mayoría no tiene cuenta de participante."""
    vol = crear_voluntario(status="pendiente", email="sola@ejemplo.com")

    r = client.post(f"/voluntarios/{vol.id}/approve")

    assert r.status_code == 200
    assert r.json()["status"] == "activo"


# ── Control: la puerta que ya estaba bien sigue estándolo ──────────────────

def test_habilitar_desde_la_base_sigue_apagando_el_login(client, db, participante_registrado):
    """`enroll-from-db` ya lo hacía. Que el cambio no se lo haya llevado puesto."""
    prof = db.query(ParticipantProfile).first()

    r = client.post("/voluntarios/enroll-from-db", json={
        "persona_id": prof.id, "name": "Ana", "last_name": "Diaz",
        "email": "ana@ejemplo.com",
    })

    assert r.status_code == 201
    part = db.query(Participant).filter(Participant.email == "ana@ejemplo.com").first()
    db.refresh(part)
    assert part.is_active is False


def test_invitar_a_un_voluntario_como_participante_sigue_frenado(client, db, crear_voluntario):
    """La dirección contraria ya estaba tapada en `/invite`."""
    v = crear_voluntario(email="beto@ejemplo.com", name="Beto")
    prof = ParticipantProfile(
        name="Beto", email="beto@ejemplo.com", is_volunteer=True, volunteer_id=v.id,
    )
    db.add(prof)
    db.commit()

    r = client.post("/participants/invite", json={"profile_id": prof.id})

    assert r.status_code == 409
