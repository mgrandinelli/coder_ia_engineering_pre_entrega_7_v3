---
titulo: Arquitectura de la plataforma Atlas
categoria: arquitectura
etiquetas: microservicios, rabbitmq, redis, postgresql
---

# Plataforma Atlas: arquitectura

## Visión general

Atlas es la plataforma interna de pagos de Norte Digital. Procesa los cobros de las tiendas en línea de la empresa y expone una API única para que los equipos de producto no tengan que integrarse por separado con cada medio de pago. La plataforma está formada por siete microservicios que se comunican entre sí de dos maneras: llamadas HTTP sincrónicas para las operaciones que el cliente espera en pantalla, y eventos asincrónicos para todo lo que puede resolverse en segundo plano.

El punto de entrada es el servicio `gateway`, escrito en Go. Recibe todas las solicitudes externas, valida el token de acceso, aplica los límites de tráfico y deriva cada pedido al servicio que corresponde. Ningún otro servicio es accesible desde fuera de la red interna.

## Servicios principales

El servicio `pagos` es el núcleo de la plataforma. Está escrito en Python con FastAPI y se encarga de crear, autorizar y confirmar cada cobro. Guarda su información en una base de datos PostgreSQL 16 con una réplica de lectura. Todas las consultas de reportes van a la réplica, para que no compitan con las operaciones de escritura.

El servicio `conciliacion` compara cada noche los cobros registrados por Atlas con los archivos que envían los bancos. Corre como un proceso programado a las 02:00, hora de Argentina, y tarda entre 20 y 40 minutos según el volumen del día. Las diferencias que encuentra se guardan en la tabla `diferencias` y generan un aviso para el equipo de finanzas.

El servicio `notificaciones` envía los correos y los avisos a las tiendas cuando cambia el estado de un cobro. No consulta a `pagos` directamente: escucha los eventos que este publica.

Los otros tres servicios son `antifraude`, que califica el riesgo de cada operación antes de autorizarla; `reembolsos`, que gestiona las devoluciones; y `reportes`, que arma los resúmenes diarios para las tiendas.

## Comunicación por eventos

Los eventos viajan por RabbitMQ. Cada servicio publica en un exchange propio y los interesados se suscriben con su propia cola, de modo que un consumidor lento no afecta a los demás. Los mensajes que fallan tres veces seguidas pasan a una cola de mensajes muertos, que se revisa a mano cada mañana.

Todos los eventos llevan un identificador de idempotencia. Si un servicio recibe dos veces el mismo evento, la segunda vez lo descarta. Esta regla es obligatoria: RabbitMQ garantiza que un mensaje se entrega al menos una vez, no exactamente una vez.

## Caché y límites de tráfico

El `gateway` usa Redis para dos cosas. La primera es el caché de las consultas de estado de un cobro, con una vigencia de 30 segundos. La segunda es el conteo de solicitudes por tienda: cada tienda puede hacer hasta 600 solicitudes por minuto. Al superar ese límite, el `gateway` responde con el código 429 y un encabezado que indica cuántos segundos faltan para poder reintentar.

Redis no guarda ningún dato que no pueda reconstruirse. Si la instancia se reinicia, el caché se vuelve a llenar solo y los contadores arrancan de cero.
