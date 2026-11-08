"""
El espejo: que la persona y su ficha de voluntario digan siempre lo mismo.

Una persona vinculada a ALMA puede estar en dos tablas a la vez:
`participant_profiles` (la persona, registro maestro) y `voluntarios` (su ficha
de voluntario/a). Las dos guardan nombre, apellido, teléfono y fecha de
nacimiento, cada una en su columna.

Las ALTAS ya dejaban las dos copias iguales. La EDICIÓN no: editar a alguien en
Voluntarios no avisaba a su persona, ni al revés, así que las dos fichas se iban
separando con cada cambio. El síntoma que lo destapó fue la fecha de nacimiento
—distinta en cada pantalla— y eso le importa al cron de cumpleaños, que lee
`voluntarios` y terminaba saludando según un dato que la otra pantalla
desmentía.

Lo que se prueba acá es ese enganche, y sobre todo que NO pise datos de más:
hay gente cargada del Excel con teléfono que después se habilitó como
voluntaria sin cargárselo, así que la persona tiene datos que el voluntario no.
"""
from datetime import date

import pytest

from app.models.participant import ParticipantProfile


@pytest.fixture()
def persona_voluntaria(db, crear_voluntario):
    """Una persona con ficha de voluntaria: las dos filas, ya vinculadas.

    Es el estado en el que queda alguien después de ser aprobado/a o habilitado/a
    desde Base de datos.
    """
    v = crear_voluntario(
        name="Dolores",
        last_name="Ibarra",
        email="dolores@ejemplo.com",
        phone="341-5550000",
        birth_date=date(1962, 3, 14),
    )
    p = ParticipantProfile(
        name="Dolores",
        last_name="Ibarra",
        email="dolores@ejemplo.com",
        phone="341-5550000",
        birth_date=date(1962, 3, 14),
        is_volunteer=True,
        volunteer_id=v.id,
        source="voluntario",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return v, p


# ── De Voluntarios hacia la persona ────────────────────────────────────────

def test_editar_la_fecha_de_nacimiento_la_cambia_en_las_dos(client, db, persona_voluntaria):
    """El bug original: la fecha quedaba distinta en cada pantalla."""
    v, p = persona_voluntaria

    r = client.put(f"/voluntarios/{v.id}", json={"birth_date": "1962-03-15"})

    assert r.status_code == 200
    assert r.json()["birth_date"] == "1962-03-15"
    db.refresh(p)
    assert p.birth_date == date(1962, 3, 15)


def test_editar_el_telefono_lo_cambia_en_las_dos(client, db, persona_voluntaria):
    v, p = persona_voluntaria

    client.put(f"/voluntarios/{v.id}", json={"phone": "341-5551111"})

    db.refresh(p)
    assert p.phone == "341-5551111"


def test_editar_el_nombre_lo_cambia_en_las_dos(client, db, persona_voluntaria):
    v, p = persona_voluntaria

    client.put(f"/voluntarios/{v.id}", json={"name": "Dolores Mabel"})

    db.refresh(p)
    assert p.name == "Dolores Mabel"


def test_borrar_un_dato_tambien_viaja(client, db, persona_voluntaria):
    """Vaciar un campo es una edición como cualquier otra, no un 'no mandó nada'."""
    v, p = persona_voluntaria

    client.put(f"/voluntarios/{v.id}", json={"birth_date": None})

    db.refresh(p)
    assert p.birth_date is None


# ── Lo que NO tiene que tocar ──────────────────────────────────────────────

def test_editar_solo_el_estado_no_borra_nada_de_la_persona(client, db, crear_voluntario):
    """La protección que importa.

    La persona tiene teléfono y fecha; el voluntario no. Si al guardar se
    pisaran los cuatro campos, marcarle el estado le borraría los dos datos a
    la persona. Solo viaja lo que el PUT mandó.
    """
    v = crear_voluntario(name="Hugo", email="hugo@ejemplo.com", phone=None, birth_date=None)
    p = ParticipantProfile(
        name="Hugo", last_name="Pereyra", email="hugo@ejemplo.com",
        phone="341-5552222", birth_date=date(1950, 8, 1),
        is_volunteer=True, volunteer_id=v.id,
    )
    db.add(p)
    db.commit()

    r = client.put(f"/voluntarios/{v.id}", json={"status": "inactivo"})

    assert r.status_code == 200
    db.refresh(p)
    assert p.phone == "341-5552222"
    assert p.birth_date == date(1950, 8, 1)
    assert p.last_name == "Pereyra"


def test_el_email_no_se_propaga(client, db, persona_voluntaria):
    """Es la credencial de login y tiene UNIQUE en las dos tablas."""
    v, p = persona_voluntaria

    client.put(f"/voluntarios/{v.id}", json={"email": "otro@ejemplo.com"})

    db.refresh(p)
    assert p.email == "dolores@ejemplo.com"


def test_los_campos_propios_del_voluntario_no_llegan_a_la_persona(client, db, persona_voluntaria):
    """`specialties`, `status` y `is_admin` no existen del otro lado."""
    v, p = persona_voluntaria

    r = client.put(
        f"/voluntarios/{v.id}",
        json={"specialties": ["Acompañamiento"], "status": "activo"},
    )

    assert r.status_code == 200
    assert not hasattr(p, "specialties")


# ── De Base de datos hacia la ficha de voluntario ──────────────────────────

def test_editar_la_persona_cambia_la_ficha_del_voluntario(client, db, persona_voluntaria):
    v, p = persona_voluntaria

    r = client.put(f"/personas/{p.id}", json={"birth_date": "1962-03-20"})

    assert r.status_code == 200
    db.refresh(v)
    assert v.birth_date == date(1962, 3, 20)


def test_editar_la_persona_no_pisa_lo_que_no_mando(client, db, persona_voluntaria):
    v, p = persona_voluntaria

    client.put(f"/personas/{p.id}", json={"city": "Rosario"})

    db.refresh(v)
    assert v.phone == "341-5550000"
    assert v.birth_date == date(1962, 3, 14)


def test_editar_el_email_de_la_persona_no_toca_al_voluntario(client, db, persona_voluntaria):
    v, p = persona_voluntaria

    client.put(f"/personas/{p.id}", json={"email": "dolores.nueva@ejemplo.com"})

    db.refresh(v)
    assert v.email == "dolores@ejemplo.com"


# ── Los casos en que no hay nadie del otro lado ────────────────────────────

def test_un_voluntario_pendiente_sin_persona_se_edita_igual(client, crear_voluntario):
    """Entra al directorio maestro recién al ser aprobado: hasta entonces no
    tiene espejo, y eso no puede hacer fallar el guardado."""
    v = crear_voluntario(status="pendiente", email="recien@ejemplo.com")

    r = client.put(f"/voluntarios/{v.id}", json={"phone": "341-5553333"})

    assert r.status_code == 200
    assert r.json()["phone"] == "341-5553333"


def test_el_pendiente_no_se_le_crea_persona_al_editarlo(client, db, crear_voluntario):
    """Editarlo no lo mete en la base de personas por la ventana: eso pasa al
    aprobarlo, no antes."""
    v = crear_voluntario(status="pendiente", email="recien@ejemplo.com")

    client.put(f"/voluntarios/{v.id}", json={"name": "Recienvenido"})

    assert db.query(ParticipantProfile).count() == 0


def test_una_persona_sin_ficha_de_voluntario_se_edita_igual(client):
    r = client.post("/personas/", json={"name": "Sola", "email": "sola@ejemplo.com"})
    persona_id = r.json()["id"]

    r = client.put(f"/personas/{persona_id}", json={"birth_date": "1970-01-05"})

    assert r.status_code == 200
    assert r.json()["birth_date"] == "1970-01-05"


def test_el_vinculo_se_sigue_por_volunteer_id_y_no_por_email(client, db, persona_voluntaria):
    """Si alguien le cambió el mail al voluntario, el espejo tiene que seguir
    encontrándose. Buscarlo por email devolvería None (o, peor, otra persona)."""
    v, p = persona_voluntaria
    v.email = "mail-nuevo@ejemplo.com"
    db.commit()

    client.put(f"/voluntarios/{v.id}", json={"phone": "341-5554444"})

    db.refresh(p)
    assert p.phone == "341-5554444"
