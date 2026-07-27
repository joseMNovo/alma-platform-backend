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

Si estuvieras montando la base desde cero, este es el orden. Cada script es
idempotente o está pensado para correrse una sola vez; los marcados con ⚠️
tienen `ALTER` que fallan si ya se aplicaron (revisar antes de repetir).

| # | Archivo | Qué crea / cambia |
|---|---|---|
| 1 | `ideas.sql` | `ideas` + `idea_comments` |
| 2 | `announcements.sql` | `announcements` + `announcement_dismissals` (popup al ingresar) |
| 3 | ⚠️ `personas.sql` | Convierte `participant_profiles` en el registro maestro de personas: login opcional, email como clave, socio/a, rol voluntario |
| 4 | `group_histories.sql` | `group_histories` + `group_history_attendees` (minutas de grupos) |
| 5 | `activity_events.sql` | `activity_events` (tracking de uso de módulos) |
| 6 | `push_subscriptions.sql` | `push_subscriptions` + `notifications` (Web Push y campanita) |
| 7 | ⚠️ `add_created_by_to_actividades.sql` | Agrega `created_by` a `actividades` |
| 8 | `files.sql` | `files` — almacén genérico de archivos (metadata; los bytes van a disco) |
| 9 | `capacitaciones.sql` | `trainings`, `training_items`, `person_access_grants`, `person_payments`, `training_item_progress`, `training_item_views`, `access_audit` |
| 10 | `participant_enrollments.sql` | `participant_program_enrollments` (inscripción de participantes a talleres/grupos/actividades). El modelo y los endpoints ya existían; la tabla faltaba |

`files.sql` va **antes** que `capacitaciones.sql`: la portada de una
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
