import html as html_lib
import resend
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from config import settings
from app.database import SessionLocal
from app.models.email_log import EmailLog
from app.schemas.email_log import SendEmailRequest
from app.utils.logger import log_error


_BASE = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:ital,wght@0,300;0,400;0,600;0,700&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#F4F4F4;font-family:'Nunito Sans',Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
         style="background:#F4F4F4;padding:48px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
             style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;
                    box-shadow:0 2px 12px rgba(0,0,0,.07);">

        <!-- Header: logo sobre blanco -->
        <tr>
          <td style="background:#ffffff;padding:48px 48px 32px;text-align:center;">
            <img src="https://comunidadalma.org.ar/images/alma_aqua.png"
                 alt="ALMA Alzheimer Rosario" width="180" height="auto"
                 style="display:block;margin:0 auto;max-width:180px;" />
          </td>
        </tr>

        <!-- Divisor sutil -->
        <tr>
          <td style="padding:0 48px;">
            <div style="height:1px;background:#F0F0F0;"></div>
          </td>
        </tr>

        <!-- Cuerpo -->
        <tr>
          <td style="padding:40px 48px 48px;background:#ffffff;text-align:center;">
            {{body_html}}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:0 48px 40px;text-align:center;">
            <div style="height:1px;background:#E0E0E0;margin-bottom:24px;"></div>
            <p style="margin:0;color:#9A9A9A;font-size:12px;line-height:1.6;
                      font-family:'Nunito Sans',Arial,sans-serif;">
              <a href="mailto:hola@almarosario.org.ar"
                 style="color:#9A9A9A;text-decoration:none;">hola@almarosario.org.ar</a>
              &nbsp;&middot;&nbsp;
              <a href="https://almarosario.org.ar"
                 style="color:#9A9A9A;text-decoration:none;">almarosario.org.ar</a>
            </p>
            <p style="margin:8px 0 0;color:#BBBBBB;font-size:11px;
                      font-family:'Nunito Sans',Arial,sans-serif;">
              Este es un mensaje automático. Por favor no respondas a este email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


_BODIES: dict[str, str] = {
    "verification": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  ¡Bienvenido/a,<br>{{name}}!
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Gracias por registrarte en ALMA. Para activar tu cuenta<br>confirmá tu dirección de email.
</p>
<p style="margin:0 0 36px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  El link es válido por {{expiry}}.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{verification_url}}"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Verificar email
      </a>
    </td>
  </tr>
</table>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Si no creaste esta cuenta podés ignorar este email.
</p>""",

    "pin_reset": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Restablecer<br>tu PIN
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Recibimos una solicitud para restablecer el PIN<br>de tu cuenta ALMA.
</p>
<p style="margin:0 0 36px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  El link es válido por {{expiry_hours}} horas.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{reset_url}}"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Restablecer PIN
      </a>
    </td>
  </tr>
</table>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Si no solicitaste este cambio podés ignorar este email.
</p>""",

    # Invitación a la plataforma iniciada por un admin desde la Base de datos.
    # A diferencia de "verification" (auto-registro, la persona ya eligió PIN),
    # acá la persona NO tiene PIN todavía: el link la lleva a crearlo. Reusa el
    # flujo de restablecer-pin, por eso el botón dice "Crear mi PIN".
    "invitation": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Te sumamos a<br>Comunidad ALMA
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>,
  <strong style="font-weight:600;color:#4D4D4D;">{{registered_by}}</strong> te invitó<br>
  a crear tu cuenta en la plataforma de ALMA.
</p>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Elegí un PIN de 4 dígitos y ya vas a poder ingresar para ver tus<br>
  actividades, capacitaciones y novedades.
</p>
<p style="margin:0 0 36px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  El link es válido por {{expiry_hours}} horas.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{invite_url}}"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Crear mi PIN e ingresar
      </a>
    </td>
  </tr>
</table>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Si no esperabas esta invitación podés ignorar este email.
</p>""",

    # Conversión de una persona que YA tenía cuenta de participante: pasa a
    # voluntaria conservando su PIN. Sin "pendiente", sin pedirle un PIN nuevo.
    "volunteer_converted": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Ahora sos<br>voluntario/a de ALMA
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>,
  <strong style="font-weight:600;color:#4D4D4D;">{{registered_by}}</strong> te sumó<br>
  al equipo de voluntarios/as.
</p>
<p style="margin:0 0 36px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Ingresá con el <strong style="font-weight:600;color:#4D4D4D;">mismo PIN de siempre</strong>:
  no cambió nada,<br>ahora vas a ver las herramientas de voluntario/a.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{app_url}}"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Ingresar
      </a>
    </td>
  </tr>
</table>""",

    "new_volunteer": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Nueva solicitud<br>de voluntario/a
</h1>
<p style="margin:0 0 24px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong> se registró en ALMA<br>
  y está esperando tu aprobación.
</p>
<table role="presentation" cellspacing="0" cellpadding="0"
       style="margin:0 auto 32px;background:#F8F8F8;border-radius:8px;width:100%;max-width:360px;">
  <tr>
    <td style="padding:20px 24px;">
      <p style="margin:0 0 8px;color:#9A9A9A;font-size:12px;font-weight:600;
                text-transform:uppercase;letter-spacing:0.5px;">Email</p>
      <p style="margin:0;color:#4D4D4D;font-size:14px;font-weight:400;">{{email}}</p>
    </td>
  </tr>
</table>
<p style="margin:0 0 28px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  Ingresá a la plataforma ALMA para revisar<br>y aprobar la solicitud.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{app_url}}/aprobaciones"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Ver aprobaciones
      </a>
    </td>
  </tr>
</table>""",

    "volunteer_pending": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  ¡Te sumamos<br>como voluntario/a!
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>,
  <strong style="font-weight:600;color:#4D4D4D;">{{registered_by}}</strong> te registró<br>
  como voluntario/a en ALMA.
</p>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Tu solicitud quedó <strong style="font-weight:600;color:#4D4D4D;">pendiente de aprobación</strong><br>
  por el equipo de ALMA.
</p>
<p style="margin:0 0 8px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  Te avisaremos por este mismo medio cuando tu cuenta<br>esté aprobada y puedas crear tu PIN de acceso.
</p>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Gracias por sumarte a ALMA. 💙
</p>""",

    "approved": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  ¡Tu cuenta<br>fue aprobada!
</h1>
<p style="margin:0 0 12px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>, el equipo de ALMA<br>aprobó tu solicitud como voluntario/a.
</p>
<p style="margin:0 0 36px;color:#AAAAAA;font-size:13px;line-height:1.6;font-weight:300;">
  El último paso es crear tu PIN de acceso.<br>El link es válido por 24 horas.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{pin_reset_url}}"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Creá tu PIN
      </a>
    </td>
  </tr>
</table>""",

    # Recordatorio AUTOMÁTICO del cron (alma_cron.py). Lo reciben tanto los
    # voluntarios asignados como los participantes anotados: por eso la primera
    # línea no dice "asignada" fija, sino {{event_phrase}}, que el cron arma
    # según a quién le escribe y de qué tipo de evento se trata.
    #
    # {{event_name}} es el nombre real del encuentro y es lo que más importa:
    # sin él, el mail obliga a abrirlo para saber si te interesa. {{event_label}}
    # (el tipo) viaja VACÍO cuando el evento no tiene nombre propio, porque en
    # ese caso {{event_name}} ya cae al tipo y se leería dos veces.
    "event_assignment": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Recordatorio
</h1>
<p style="margin:0 0 24px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>, te recordamos que
  tenés {{event_phrase}} <strong style="font-weight:600;color:#4D4D4D;">{{when_label}}</strong>.
</p>
<table role="presentation" cellspacing="0" cellpadding="0"
       style="margin:0 auto 32px;background:#F8F8F8;border-radius:8px;width:100%;max-width:360px;">
  <tr>
    <td style="padding:20px 24px;text-align:left;">
      <p style="margin:0 0 4px;color:#9A9A9A;font-size:12px;font-weight:600;
                text-transform:uppercase;letter-spacing:0.5px;">{{event_label}}</p>
      <p style="margin:0;color:#4D4D4D;font-size:18px;font-weight:700;line-height:1.35;">{{event_name}}</p>
      <p style="margin:6px 0 0;color:#6B6B6B;font-size:15px;font-weight:600;">{{event_date}} &middot; {{event_time}} hs</p>
      <p style="margin:8px 0 0;color:#6B6B6B;font-size:13px;font-weight:300;">{{notes}}</p>
    </td>
  </tr>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{app_url}}/calendarios"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Ver el calendario
      </a>
    </td>
  </tr>
</table>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Gracias por tu compromiso con ALMA. 💙
</p>""",

    # Recordatorio de evento DISPARADO A MANO por un admin (botón del calendario).
    # Neutral: sirve tanto para voluntarios como para participantes.
    "event_reminder": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  Recordatorio
</h1>
<p style="margin:0 0 24px;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;">
  Hola <strong style="font-weight:600;color:#4D4D4D;">{{name}}</strong>, te recordamos que
  <strong style="font-weight:600;color:#4D4D4D;">{{event_label}}</strong> es
  <strong style="font-weight:600;color:#4D4D4D;">{{when_label}}</strong>.
</p>
<table role="presentation" cellspacing="0" cellpadding="0"
       style="margin:0 auto 32px;background:#F8F8F8;border-radius:8px;width:100%;max-width:360px;">
  <tr>
    <td style="padding:20px 24px;text-align:left;">
      <p style="margin:0 0 4px;color:#9A9A9A;font-size:12px;font-weight:600;
                text-transform:uppercase;letter-spacing:0.5px;">{{event_label}}</p>
      <p style="margin:0;color:#4D4D4D;font-size:16px;font-weight:600;">{{event_date}} &middot; {{event_time}} hs</p>
      <p style="margin:8px 0 0;color:#6B6B6B;font-size:13px;font-weight:300;">{{notes}}</p>
    </td>
  </tr>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:0 auto;">
  <tr>
    <td style="border-radius:7px;background:#5EC0CF;">
      <a href="{{app_url}}/calendarios"
         style="display:inline-block;padding:11px 28px;color:#ffffff;text-decoration:none;
                font-size:14px;font-weight:600;border-radius:7px;letter-spacing:0.3px;
                font-family:'Nunito Sans',Arial,sans-serif;">
        Ver en la Agenda
      </a>
    </td>
  </tr>
</table>
<p style="margin:32px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Nos vemos pronto. 💙 ALMA
</p>""",

    # Aviso escrito por un admin desde "Anuncios". El texto lo pone quien
    # escribe, así que va a `{{message}}` con los saltos de línea ya
    # convertidos a <br> (ver send_announcement_emails).
    "announcement": """\
<h1 style="margin:0 0 16px;color:#9A8BC2;font-size:28px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  {{title}}
</h1>
<p style="margin:0;color:#6B6B6B;font-size:15px;line-height:1.8;font-weight:300;text-align:left;">
  {{message}}
</p>""",

    # Alerta técnica interna (NO va a usuarios) — avisa si el cron falla.
    "cron_alert": """\
<h1 style="margin:0 0 16px;color:#C0392B;font-size:24px;font-weight:700;line-height:1.3;
           font-family:'Nunito Sans',Arial,sans-serif;">
  ⚠️ Falló el cron de recordatorios
</h1>
<p style="margin:0 0 8px;color:#6B6B6B;font-size:15px;line-height:1.7;font-weight:300;">
  {{reason}}
</p>
<p style="margin:0 0 20px;color:#AAAAAA;font-size:13px;font-weight:300;">
  {{timestamp}}
</p>
<table role="presentation" cellspacing="0" cellpadding="0"
       style="margin:0 0 20px;background:#FBEAEA;border-left:4px solid #C0392B;border-radius:6px;width:100%;">
  <tr>
    <td style="padding:14px 18px;text-align:left;">
      <p style="margin:0 0 6px;color:#C0392B;font-size:12px;font-weight:700;
                text-transform:uppercase;letter-spacing:0.5px;">Mensaje del error</p>
      <p style="margin:0;color:#4D4D4D;font-size:14px;font-weight:600;
                font-family:'Courier New',monospace;word-break:break-word;">{{error_message}}</p>
    </td>
  </tr>
</table>
<p style="margin:0 0 6px;color:#9A9A9A;font-size:12px;font-weight:600;
          text-transform:uppercase;letter-spacing:0.5px;">Detalle técnico</p>
<pre style="margin:0;padding:14px 16px;background:#2D2D2D;color:#E0E0E0;border-radius:6px;
            font-family:'Courier New',monospace;font-size:12px;line-height:1.5;
            white-space:pre-wrap;word-break:break-word;overflow-x:auto;">{{traceback}}</pre>
<p style="margin:24px 0 0;color:#BBBBBB;font-size:12px;font-weight:300;">
  Mensaje automático del servidor de ALMA. Revisá el log del cron en la VPS.
</p>""",
}


def _render(template: str | None, variables: dict, body: str | None) -> str:
    if template and template in _BODIES:
        html_body = _BODIES[template]
    elif body:
        html_body = body
    else:
        html_body = "<p>Sin contenido.</p>"

    for key, value in variables.items():
        html_body = html_body.replace(f"{{{{{key}}}}}", str(value))

    return _BASE.replace("{{body_html}}", html_body)


def _deliver_to_resend(req: SendEmailRequest) -> tuple[str, str | None, str | None]:
    """Entrega el mail por Resend. Devuelve (status, resend_id, error_message).

    NUNCA lanza: un fallo de Resend se refleja en el status, no rompe al llamador.
    Esta es la parte lenta (depende de un tercero); todo lo que la llame de forma
    directa se bloquea hasta que Resend conteste.
    """
    resend.api_key = settings.RESEND_API_KEY

    html = _render(req.template, req.variables or {}, req.body)

    try:
        params: dict = {
            "from": settings.MAIL_FROM,
            "to": req.to,
            "subject": req.subject,
            "html": html,
        }
        if req.cc:
            params["cc"] = req.cc
        if req.bcc:
            params["bcc"] = req.bcc

        response = resend.Emails.send(params)
        return "sent", response.get("id"), None
    except Exception as e:
        return "failed", None, str(e)


def _build_log(
    req: SendEmailRequest,
    status: str,
    resend_id: str | None = None,
    error_message: str | None = None,
) -> EmailLog:
    return EmailLog(
        # `sent_at` es el momento del INTENTO: se fija igual para 'sent' y para
        # 'failed' (es la columna por la que ordena `GET /emails/logs`).
        sent_at=datetime.now(timezone.utc),
        from_address=settings.MAIL_FROM,
        to_addresses=req.to,
        cc=req.cc,
        bcc=req.bcc,
        subject=req.subject,
        template=req.template,
        status=status,
        resend_id=resend_id,
        sent_by_volunteer_id=req.sent_by_volunteer_id,
        error_message=error_message,
        variables=req.variables,
    )


def send_email(db: Session, req: SendEmailRequest) -> EmailLog:
    """Envío SÍNCRONO: espera a Resend y devuelve el EmailLog ya resuelto.

    Bloquea al llamador todo lo que tarde Resend. Desde un request HTTP eso hay
    que tenerlo en cuenta: quien llame de afuera necesita un timeout generoso
    (`alma_cron.py` usa 90s). Para disparar y olvidarse está `send_email_bg`.
    """
    status, resend_id, error_message = _deliver_to_resend(req)

    log = _build_log(req, status, resend_id, error_message)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def send_email_bg(req: SendEmailRequest) -> None:
    """`send_email` para usar con BackgroundTasks: abre su PROPIA sesión.

    Desde FastAPI 0.106 las dependencias con `yield` se cierran ANTES de que
    corran las tareas de background, así que la sesión del request ya no sirve
    acá. Nunca lanza: una tarea de background que revienta no tiene a nadie que
    la escuche.
    """
    db = SessionLocal()
    try:
        send_email(db, req)
    except Exception:
        db.rollback()
        log_error(
            "Fallo al registrar el email enviado en background",
            module="emails", action="send_email_bg",
            meta={"template": req.template, "to_count": len(req.to)}, exc_info=True,
        )
    finally:
        db.close()


# Resend acepta hasta 50 direcciones por envío. Se manda de a 45 para dejar
# lugar al `to` y no quedar al filo del límite.
_TAMANO_LOTE = 45


def send_announcement_emails(
    emails: list[str],
    subject: str,
    title: str,
    message: str,
    sent_by_volunteer_id: int | None = None,
) -> None:
    """Manda un anuncio por mail a una lista de direcciones. Para background.

    Va con copia oculta y en lotes: en un anuncio a toda la organización, poner
    a todo el mundo en el `to` filtraría la lista de contactos de ALMA a
    cualquiera que lo reciba.

    El texto lo escribe una persona en un textarea, así que se escapa el HTML y
    recién ahí se convierten los saltos de línea: sin eso, un `<` en el mensaje
    rompe el mail (y peor, deja inyectar etiquetas).
    """
    limpios = [e.strip() for e in emails if e and e.strip()]
    if not limpios:
        return

    cuerpo = html_lib.escape(message).replace("\n", "<br>")
    variables = {"title": html_lib.escape(title), "message": cuerpo}

    for i in range(0, len(limpios), _TAMANO_LOTE):
        lote = limpios[i : i + _TAMANO_LOTE]
        send_email_bg(
            SendEmailRequest(
                to=[settings.MAIL_FROM],
                bcc=lote,
                subject=subject,
                template="announcement",
                variables=variables,
                sent_by_volunteer_id=sent_by_volunteer_id,
            )
        )


