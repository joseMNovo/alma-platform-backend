# Scripts SQL — ALMA Platform

Migraciones de esquema, **para ejecutar a mano** contra la base MySQL.
No hay ninguna herramienta de migraciones automática: se corren desde el
cliente MySQL cuando se despliega la funcionalidad que las necesita.

Viven en el backend porque **FastAPI es lo único que toca MySQL**. El frontend
nunca abre una conexión: pasa siempre por la API.

> Los `.sql` están en `.gitignore` (regla `*.sql`), así que **no se versionan**.
> Este archivo sí, para que la convención y el orden queden documentados.
> Si perdés los archivos, el esquema se recupera del `mysqldump` del backup.

## Orden de ejecución

**El número del archivo ES el orden.** Para saber cuál sigue, mirá el último
que corriste y seguí por el siguiente. Van con dos dígitos para que el
listado de la carpeta quede ordenado también a partir del décimo.

Si estuvieras montando la base desde cero, este es el orden. Cada script es
idempotente o está pensado para correrse una sola vez; los marcados con ⚠️
tienen `ALTER` que fallan si ya se aplicaron (revisar antes de repetir).

| # | Archivo | Qué crea / cambia |
|---|---|---|
| 1 | `01_ideas.sql` | `ideas` + `idea_comments` |
| 2 | `02_announcements.sql` | `announcements` + `announcement_dismissals` (popup al ingresar) |
| 3 | ⚠️ `03_personas.sql` | Convierte `participant_profiles` en el registro maestro de personas: login opcional, email como clave, socio/a, rol voluntario |
| 4 | `04_group_histories.sql` | `group_histories` + `group_history_attendees` (minutas de grupos) |
| 5 | `05_activity_events.sql` | `activity_events` (tracking de uso de módulos) |
| 6 | `06_push_subscriptions.sql` | `push_subscriptions` + `notifications` (Web Push y campanita) |
| 7 | ⚠️ `07_add_created_by_to_actividades.sql` | Agrega `created_by` a `actividades` |
| 8 | `08_files.sql` | `files` — almacén genérico de archivos (metadata; los bytes van a disco) |
| 9 | `09_capacitaciones.sql` | `trainings`, `training_items`, `person_access_grants`, `person_payments`, `training_item_progress`, `training_item_views`, `access_audit` |
| 10 | `10_participant_enrollments.sql` | `participant_program_enrollments` (inscripción de participantes a talleres/grupos/actividades). El modelo y los endpoints ya existían; la tabla faltaba |
| 11 | `11_capacitaciones_payment_url.sql` | Agrega `trainings.payment_url` (link de pago de MercadoPago para el botón de compra) |
| 12 | `12_certificados.sql` | `certificate_templates` (redacción editable del certificado) + `trainings.certificate_template_id` + `participant_profiles.dni` |
| 13 | `13_configuracion.sql` | `app_settings` (ajustes clave/valor editables desde la app; el primero es el link de pago general de capacitaciones) |
| 14 | `14_capacitaciones_carga_horaria.sql` | Agrega `trainings.certificate_hours` (la carga horaria que se imprime en el certificado) |
| 15 | `15_encuestas.sql` | `surveys`, `survey_questions`, `survey_options`, `survey_attempts`, `survey_answers` (evaluaciones con nota y encuestas de opinión) |
| 16 | `16_certificados_emitidos.sql` | `certificates` — los certificados entregados, con el texto congelado y el código de verificación |
| 17 | `17_recordatorios_participantes.sql` | `participant_event_reminders` + `participant_reminder_sent_log` (cada participante elige sus avisos por evento) |
| 18 | `18_encuestas_justificacion.sql` | Agrega `survey_questions.explanation` (el porqué de la respuesta, se muestra al terminar) |
| 19 | `19_training_items_intro_gratis.sql` | Agrega `training_items.is_free_preview` (el video de introducción que se ve sin pagar y sin cuenta, en la landing pública) |

`15_encuestas.sql` va **antes** que `16_certificados_emitidos.sql`: un certificado
referencia el intento de evaluación que lo habilitó.

`08_files.sql` va **antes** que `09_capacitaciones.sql`: la portada de una
capacitación referencia un `guid` de `files`.

## Cómo correrlos

```bash
mysql -u <usuario> -p <base> < sql/ideas.sql
```

O pegando el contenido en el cliente que uses. Después de cada uno, verificar
con las consultas de control que están comentadas al final de cada script.

## Antes de tocar producción

1. **Backup primero.** Siempre. Ver `PROMPT-VPS.md` en el frontend.
2. Probar el script en desarrollo.
3. Los `ALTER` sobre tablas con datos no son reversibles con un Ctrl+Z.
