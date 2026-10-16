---
titulo: Observabilidad de la plataforma Atlas
categoria: operaciones
etiquetas: metricas, trazas, logs, grafana
---

# Plataforma Atlas: observabilidad

## Tres tipos de señales

Para entender qué pasa dentro de Atlas se usan tres tipos de señales: métricas, registros y trazas. Las métricas responden cuánto y con qué frecuencia; los registros cuentan qué ocurrió en un momento dado; las trazas muestran el recorrido completo de una solicitud a través de varios servicios.

Todos los servicios emiten las tres señales con OpenTelemetry. El colector de OpenTelemetry corre como un agente en cada nodo del clúster y reenvía cada señal a su destino, de manera que los servicios no necesitan conocer adónde van los datos.

## Métricas

Las métricas se guardan en Prometheus, con una retención de 15 días en detalle y de 13 meses en forma resumida. Cada servicio expone como mínimo las cuatro señales doradas: latencia, tráfico, errores y saturación.

Los tableros están en Grafana. El tablero principal se llama "Atlas: salud general" y muestra, para cada servicio, la tasa de errores y la latencia del percentil 95 de los últimos 30 minutos. Es el primer lugar que se mira durante un incidente.

## Registros

Los registros se escriben en formato JSON, una línea por evento, y se envían a Loki. Cada línea incluye el nombre del servicio, el nivel, el identificador de la traza y el identificador de la tienda. Se conservan durante 30 días.

Está prohibido registrar datos personales o de pago. Para depurar un cobro se usa su identificador, nunca los datos del comprador.

## Trazas

Las trazas se consultan en Tempo. Cuando una solicitud es lenta, la traza permite ver en qué servicio se fue el tiempo: cada tramo indica cuánto tardó y a qué servicio llamó después. Para encontrar la traza de una solicitud concreta se busca por el encabezado `X-Atlas-Trace-Id`, que el `gateway` devuelve en todas las respuestas.

Guardar todas las trazas sería demasiado costoso, así que se aplica un muestreo: se conserva el 10 % de las solicitudes normales y el 100 % de las que terminan en error o tardan más de 2 segundos.

## Objetivos de nivel de servicio

Atlas tiene dos objetivos de nivel de servicio, medidos en ventanas de 30 días. El primero es de disponibilidad: el 99,95 % de las solicitudes de cobro deben responder sin error del servidor. El segundo es de latencia: el 99 % de las solicitudes de cobro deben responder en menos de 1,5 segundos.

Cada objetivo tiene un presupuesto de error, que es el margen de fallas que se puede consumir sin incumplirlo. Si en una ventana se consume más del 75 % del presupuesto, se suspenden los despliegues de funciones nuevas y el equipo se dedica a mejorar la confiabilidad hasta que el consumo vuelva a bajar.

## Pruebas sintéticas

Además de observar el tráfico real, un proceso externo ejecuta cada minuto un cobro de prueba por un importe mínimo contra una tienda ficticia. Si tres pruebas seguidas fallan, se dispara una alerta aunque las métricas internas no muestren nada raro. Así se detectan los problemas que impiden que el tráfico llegue a la plataforma, como un error de DNS o un certificado público vencido.
