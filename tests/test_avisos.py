"""`/avisos` — qué le mandó el sistema a quién.

Lo que se prueba acá no es "devuelve 200": es que la pantalla no mienta sobre
a quién le llegó algo. Un listado de envíos que pone el nombre equivocado, o
que se come un destinatario de un mail con copia, es peor que no tener la
pantalla — porque se le cree.
"""

from datetime import datetime, timedelta

import pytest

from app.models.email_log import EmailLog
from app.models.notification import Notification
from app.models.participant import Participant, ParticipantProfile


@pytest.fixture()
def mail(db):
    """Fábrica de filas de `email_logs`."""
    contador = {"n": 0}

    def _crear(to, template="event_assignment", status="sent", dias_atras=0, **extra):
        contador["n"] += 1
        fila = EmailLog(
            sent_at=datetime.now() - timedelta(days=dias_atras),
            from_address="alma@tests.local",
            to_addresses=to,
            subject=extra.pop("subject", f"Asunto {contador['n']}"),
            template=template,
            status=status,
            **extra,
        )
        db.add(fila)
        db.commit()
        db.refresh(fila)
        return fila

    return _crear


@pytest.fixture()
def campanita(db):
    def _crear(user_type, user_id, kind="calendar_reminder", dias_atras=0, **extra):
        fila = Notification(
            user_type=user_type,
            user_id=user_id,
            title=extra.pop("title", "Te espera un evento"),
            kind=kind,
            created_at=datetime.now() - timedelta(days=dias_atras),
            **extra,
        )
        db.add(fila)
        db.commit()
        db.refresh(fila)
        return fila

    return _crear


def test_junta_las_dos_fuentes_en_una_sola_lista(client, mail, campanita, crear_voluntario):
    v = crear_voluntario(name="Ana", last_name="López", email="ana@tests.local")
    mail(["ana@tests.local"])
    campanita("voluntario", v.id)

    avisos = client.get("/avisos/").json()

    canales = {a["canal"] for a in avisos}
    assert canales == {"email", "campanita"}, "las dos tablas tienen que verse juntas"


def test_le_pone_el_nombre_a_la_direccion(client, mail, crear_voluntario):
    crear_voluntario(name="Ana", last_name="López", email="ana@tests.local")
    mail(["ana@tests.local"])

    aviso = client.get("/avisos/?canal=email").json()[0]

    assert aviso["destinatario"]["nombre"] == "Ana López"
    assert aviso["destinatario"]["tipo"] == "voluntario"


def test_una_direccion_sin_persona_se_muestra_igual(client, mail):
    """Un mail a alguien que no está cargado SALIÓ. Esconderlo por no poder
    nombrarlo haría que la pantalla miente por omisión."""
    mail(["alguien@afuera.com"])

    avisos = client.get("/avisos/?canal=email").json()

    assert len(avisos) == 1
    assert avisos[0]["destinatario"]["email"] == "alguien@afuera.com"
    assert avisos[0]["destinatario"]["nombre"] is None


def test_un_mail_a_varios_es_una_fila_por_persona(client, mail, crear_voluntario):
    crear_voluntario(name="Ana", last_name="López", email="ana@tests.local")
    crear_voluntario(name="Beto", last_name="Paz", email="beto@tests.local")
    mail(["ana@tests.local", "beto@tests.local"])

    avisos = client.get("/avisos/?canal=email").json()

    assert len(avisos) == 2, "si no, buscar a Beto no lo encuentra"
    assert {a["destinatario"]["nombre"] for a in avisos} == {"Ana López", "Beto Paz"}
    assert len({a["id"] for a in avisos}) == 2, "los ids tienen que ser únicos"


def test_el_participante_se_nombra_desde_su_perfil(client, db, campanita):
    """`notifications.user_id` apunta al login, pero el nombre vive en el
    perfil. Entrar por la tabla equivocada deja la fila sin nombre."""
    login = Participant(email="pepa@tests.local", is_active=True)
    db.add(login)
    db.commit()
    db.refresh(login)
    db.add(ParticipantProfile(participant_id=login.id, name="Pepa", last_name="Ruiz",
                              email="pepa@tests.local", is_member=False,
                              accepts_notifications=False, accepts_whatsapp=False))
    db.commit()

    campanita("participante", login.id)

    aviso = client.get("/avisos/?canal=campanita").json()[0]
    assert aviso["destinatario"]["nombre"] == "Pepa Ruiz"


def test_distingue_lo_del_cron_de_lo_que_disparo_una_persona(client, mail):
    """`sent_by_volunteer_id IS NULL` no alcanza para saberlo: los mails de
    registro también lo tienen vacío y los dispara alguien apretando un botón."""
    mail(["a@tests.local"], template="event_assignment")
    mail(["b@tests.local"], template="verificacion_email")

    avisos = {a["destinatario"]["email"]: a for a in client.get("/avisos/?canal=email").json()}

    assert avisos["a@tests.local"]["automatico"] is True
    assert avisos["b@tests.local"]["automatico"] is False


def test_solo_automaticos_deja_afuera_lo_manual(client, mail):
    mail(["a@tests.local"], template="birthday")
    mail(["b@tests.local"], template="verificacion_email")

    avisos = client.get("/avisos/?canal=email&solo_automaticos=true").json()

    assert [a["destinatario"]["email"] for a in avisos] == ["a@tests.local"]


def test_un_mail_fallido_se_ve_como_fallido(client, mail):
    mail(["a@tests.local"], status="failed", error_message="Resend dijo que no")

    aviso = client.get("/avisos/?canal=email").json()[0]

    assert aviso["estado"] == "fallido"
    assert aviso["error"] == "Resend dijo que no"


def test_la_campanita_informa_si_la_vieron_no_si_se_entrego(client, campanita, crear_voluntario):
    """Una campanita no se entrega, se deja puesta: lo único que se sabe de
    ella es si la persona la miró."""
    v = crear_voluntario()
    campanita("voluntario", v.id, is_read=True)

    aviso = client.get("/avisos/?canal=campanita").json()[0]

    assert aviso["estado"] == "leido"


def test_el_rango_de_dias_corta(client, mail):
    mail(["viejo@tests.local"], dias_atras=40)
    mail(["nuevo@tests.local"], dias_atras=1)

    avisos = client.get("/avisos/?days=7").json()

    assert [a["destinatario"]["email"] for a in avisos] == ["nuevo@tests.local"]


def test_lo_mas_nuevo_va_primero(client, mail, campanita, crear_voluntario):
    """Mezclar dos fuentes y no reordenar deja todos los mails arriba y todas
    las campanitas abajo, que no es una línea de tiempo."""
    v = crear_voluntario()
    mail(["viejo@tests.local"], dias_atras=5)
    campanita("voluntario", v.id, dias_atras=1)

    avisos = client.get("/avisos/").json()

    assert avisos[0]["canal"] == "campanita"
    assert avisos[-1]["canal"] == "email"


def test_sin_nada_que_mostrar_devuelve_lista_vacia(client):
    assert client.get("/avisos/").json() == []
