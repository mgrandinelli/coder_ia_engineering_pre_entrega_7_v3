---
titulo: Gestión de incidentes de la plataforma Atlas
categoria: operaciones
etiquetas: guardias, severidad, pagerduty, alertas
---

# Plataforma Atlas: gestión de incidentes

## Niveles de severidad

Los incidentes de Atlas se clasifican en tres niveles. Un incidente SEV1 es una caída total o una situación en la que las tiendas no pueden cobrar. Un SEV2 es una degradación parcial: los cobros funcionan, pero con demoras, o falla una función secundaria como los reembolsos. Un SEV3 es un problema menor que no afecta a las tiendas, por ejemplo un reporte que se genera tarde.

Cada nivel tiene un tiempo máximo de primera respuesta: 5 minutos para un SEV1, 30 minutos para un SEV2 y un día hábil para un SEV3.

## Guardias

El equipo de plataforma mantiene una guardia rotativa de una semana, que cambia los lunes a las 10:00. Siempre hay dos personas asignadas: la guardia primaria y la secundaria. Las alertas llegan primero a la guardia primaria por PagerDuty. Si no confirma la alerta en 5 minutos, se escala a la secundaria, y si pasan otros 10 minutos sin confirmación, a la persona responsable de la plataforma.

Quien estuvo de guardia y atendió un incidente fuera del horario laboral tiene derecho a compensar esas horas durante la semana siguiente.

## Qué hacer ante un incidente

El primer paso es abrir un canal dedicado con el nombre `#inc-` seguido de la fecha y una palabra que identifique el problema. Todo lo que se investiga y se decide se escribe en ese canal, para que quede registro.

En un SEV1 se asignan tres roles. La persona que coordina toma las decisiones y es la única que autoriza cambios en producción. La persona que investiga busca la causa. La persona que comunica actualiza la página de estado cada 20 minutos y avisa a los equipos afectados. La misma persona no puede coordinar e investigar a la vez.

La prioridad es restablecer el servicio, no encontrar la causa. Si un despliegue reciente coincide con el inicio del problema, lo primero es volver a la versión anterior y recién después analizar qué falló.

## Alertas principales

Hay cuatro alertas que despiertan a la guardia. La primera se dispara cuando la tasa de cobros rechazados supera el 5 % durante 5 minutos. La segunda, cuando la latencia del percentil 95 del `gateway` supera 1 segundo durante 10 minutos. La tercera, cuando la cola de mensajes muertos de RabbitMQ acumula más de 100 mensajes. La cuarta, cuando el uso de conexiones de PostgreSQL supera el 85 % del máximo configurado.

Las demás alertas no generan una llamada: se envían al canal `#atlas-alertas` y se revisan en horario laboral.

## Revisión posterior

Todo incidente SEV1 o SEV2 termina con una revisión escrita, que debe publicarse dentro de los 5 días hábiles posteriores. El documento describe la línea de tiempo, el impacto medido en cantidad de cobros afectados, la causa y las acciones para que no se repita. Cada acción tiene una persona responsable y una fecha.

La revisión no busca culpables. Se parte de la idea de que las personas actuaron con la mejor información que tenían en ese momento, y el análisis se concentra en qué permitió que el error llegara a producción.

## Errores frecuentes

El problema más repetido en el último año fue el agotamiento del conjunto de conexiones a PostgreSQL en el servicio `pagos`, que se manifestó como respuestas 503 en los horarios de mayor tráfico. La solución fue incorporar PgBouncer delante de la base y fijar en 20 el máximo de conexiones por réplica del servicio.

El segundo problema más frecuente fue el vencimiento de certificados internos. Desde entonces, una alerta avisa 30 días antes de cada vencimiento.
