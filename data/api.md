---
titulo: API pública de la plataforma Atlas
categoria: referencia
etiquetas: api, errores, idempotencia, webhooks
---

# Plataforma Atlas: API pública

## Autenticación

Todas las solicitudes a la API deben incluir un token de acceso en el encabezado `Authorization`. El token se obtiene con las credenciales de la tienda en el punto `/v2/tokens` y tiene una validez de 60 minutos. Un token vencido produce el error `ATL-4010`; en ese caso hay que pedir uno nuevo y repetir la solicitud.

Cada tienda tiene dos juegos de credenciales: uno para el entorno de pruebas y otro para producción. Las credenciales de un entorno no funcionan en el otro.

## Crear un cobro

Un cobro se crea con una solicitud `POST` a `/v2/cobros`. El cuerpo debe indicar el importe en centavos, la moneda y el identificador del medio de pago. La respuesta incluye el identificador del cobro y su estado inicial, que puede ser `aprobado`, `rechazado` o `pendiente`.

Un cobro en estado `pendiente` todavía no tiene resultado. La tienda no debe consultarlo en un bucle: Atlas avisa el resultado final con un webhook.

## Idempotencia

Toda solicitud que crea o modifica algo debe incluir el encabezado `X-Atlas-Idempotency-Key`, con un valor único que genera la tienda. Si la misma clave llega dos veces, Atlas no repite la operación: devuelve la respuesta original. Las claves se recuerdan durante 24 horas.

Este mecanismo permite reintentar con seguridad cuando una solicitud falla por un problema de red y la tienda no sabe si el cobro llegó a crearse.

## Códigos de error

Los errores tienen un código propio de Atlas, además del código HTTP:

- `ATL-4001`: falta un campo obligatorio o tiene un formato inválido.
- `ATL-4010`: el token de acceso venció o no es válido.
- `ATL-4012`: el medio de pago fue rechazado por el banco emisor por fondos insuficientes.
- `ATL-4013`: el medio de pago fue rechazado por sospecha de fraude.
- `ATL-4290`: la tienda superó el límite de solicitudes por minuto.
- `ATL-5030`: el proveedor de cobros no respondió a tiempo.

Los errores `ATL-4290` y `ATL-5030` son transitorios y se pueden reintentar, esperando cada vez un poco más entre un intento y el siguiente. Los errores `ATL-4012` y `ATL-4013` son definitivos: reintentar con el mismo medio de pago dará el mismo resultado.

## Webhooks

Atlas notifica los cambios de estado con una solicitud `POST` a la dirección que configuró la tienda. Cada notificación lleva una firma en el encabezado `X-Atlas-Signature`, calculada con HMAC-SHA256 y el secreto de la tienda. La tienda debe verificar la firma antes de confiar en el contenido.

Si la tienda no responde con un código 2xx en 10 segundos, Atlas reintenta la notificación hasta 8 veces durante 24 horas, con una espera cada vez mayor. Pasado ese plazo, la notificación se descarta y la tienda puede consultar el estado con `GET /v2/cobros/{id}`.

## Versionado

La versión de la API figura en la ruta. La versión actual es la `v2`. Cuando se publica una versión nueva, la anterior sigue disponible durante 12 meses, y las tiendas reciben un aviso por correo 6 meses antes de que se retire.
