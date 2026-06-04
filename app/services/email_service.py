import resend
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from config import settings
from app.models.email_log import EmailLog
from app.schemas.email_log import SendEmailRequest


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
  El link es válido por {{expiry_hours}} horas.
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


def send_email(db: Session, req: SendEmailRequest) -> EmailLog:
    resend.api_key = settings.RESEND_API_KEY

    html = _render(req.template, req.variables or {}, req.body)

    status = "sent"
    resend_id = None
    error_message = None

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
        resend_id = response.get("id")
    except Exception as e:
        status = "failed"
        error_message = str(e)

    log = EmailLog(
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
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
