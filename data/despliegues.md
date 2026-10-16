---
titulo: Despliegues de la plataforma Atlas
categoria: operaciones
etiquetas: kubernetes, canary, helm, argo-cd
---

# Plataforma Atlas: despliegues

## Entornos

Atlas tiene tres entornos. El entorno de desarrollo se actualiza con cada cambio que llega a la rama principal. El entorno de pruebas, llamado `staging`, replica la configuración de producción con datos ficticios y es donde se validan las versiones candidatas. El entorno de producción atiende a las tiendas reales.

Los tres entornos corren en clústeres de Kubernetes separados. Ningún clúster comparte base de datos, colas ni secretos con otro.

## Proceso de despliegue

Cada servicio se empaqueta en una imagen de contenedor que se construye una sola vez y se promueve de un entorno al siguiente sin volver a compilarse. La imagen que llega a producción es exactamente la misma que se probó en `staging`.

El despliegue a producción usa la estrategia canary y tiene cuatro etapas. En la primera, la versión nueva recibe el 5 % del tráfico durante 15 minutos. Si la tasa de errores se mantiene por debajo del 1 %, pasa al 25 % durante otros 15 minutos, luego al 50 % y por último al 100 %. Si en cualquier etapa la tasa de errores supera el 1 % o la latencia del percentil 95 supera los 800 milisegundos, el sistema vuelve automáticamente a la versión anterior sin intervención humana.

Las instalaciones se describen con plantillas de Helm guardadas en el repositorio `atlas-infra`. Argo CD compara en forma continua lo que está instalado en el clúster con lo que dicen esas plantillas y corrige cualquier diferencia. Por eso está prohibido modificar recursos a mano con `kubectl` en producción: Argo CD deshace el cambio en pocos minutos.

## Ventanas de despliegue

Los despliegues a producción se hacen de lunes a jueves, entre las 10:00 y las 16:00, hora de Argentina. No se despliega los viernes, los fines de semana ni los días previos a un feriado. Durante las fechas de alta demanda comercial, como el Hot Sale y el Cyber Monday, rige un congelamiento: desde tres días antes y hasta un día después del evento solo se aceptan correcciones de errores críticos, y cada una requiere la aprobación de la persona responsable de la plataforma.

## Requisitos para producción

Un servicio solo puede desplegarse en producción si cumple cuatro requisitos. Los servicios críticos, que son `gateway`, `pagos` y `antifraude`, deben correr con un mínimo de 3 réplicas distribuidas en zonas de disponibilidad distintas; el resto puede correr con 2. Todos los servicios deben exponer un punto de verificación de salud en la ruta `/salud`. Las pruebas automáticas deben cubrir al menos el 80 % del código. Y cada servicio debe tener definidos sus límites de CPU y memoria.

## Migraciones de base de datos

Los cambios en el esquema de PostgreSQL se aplican con Alembic antes de desplegar la versión nueva del servicio. Toda migración debe ser compatible con la versión anterior del código, porque durante el canary conviven las dos versiones. En la práctica esto significa que una columna no se borra ni se renombra en el mismo despliegue en que deja de usarse: primero se despliega el código que ya no la necesita y, en un despliegue posterior, se elimina.

## Vuelta atrás manual

Si hace falta volver a una versión anterior fuera del proceso automático, se ejecuta el comando `atlas rollback` indicando el servicio y la versión. La operación tarda menos de dos minutos y queda registrada en el canal `#atlas-despliegues`. Las migraciones de base de datos no se revierten solas: si la versión anterior necesita el esquema viejo, la reversión debe coordinarse con el equipo de datos.
