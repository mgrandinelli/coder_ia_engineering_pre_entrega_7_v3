---
titulo: Seguridad de la plataforma Atlas
categoria: seguridad
etiquetas: vault, tls, secretos, auditoria
---

# Plataforma Atlas: seguridad

## Datos de tarjetas

Los datos de las tarjetas nunca se almacenan en Atlas. El servicio `pagos` los envía cifrados al proveedor de cobros y guarda únicamente un identificador opaco que devuelve ese proveedor. Con ese identificador se pueden hacer reembolsos y consultas, pero no se puede reconstruir el número de la tarjeta. Por este diseño, Atlas queda fuera del alcance más exigente de la norma PCI DSS: solo el proveedor de cobros debe certificar el almacenamiento.

Los registros de la aplicación pasan por un filtro que reemplaza por asteriscos cualquier secuencia de 13 a 19 dígitos seguidos, para que un número de tarjeta escrito por error en un mensaje no llegue al sistema de registros.

## Secretos y credenciales

Las claves de acceso de cada servicio se guardan en Vault y se renuevan en forma automática cada 90 días. Las credenciales de las bases de datos son todavía más cortas: Vault las genera a pedido y vencen a las 24 horas, de modo que una credencial filtrada pierde valor en un día.

Ningún secreto debe figurar en el código ni en los archivos de configuración del repositorio. Cada cambio que llega al repositorio pasa por el escáner Gitleaks, que rechaza el cambio si detecta algo con forma de clave. Si un secreto llega a publicarse igual, se considera comprometido: se lo revoca de inmediato y se abre un incidente SEV2, aunque el repositorio sea privado.

## Comunicación entre servicios

Todas las comunicaciones entre servicios usan TLS mutuo: cada servicio presenta un certificado propio y verifica el del otro extremo antes de intercambiar datos. Los certificados los emite una autoridad interna, duran 30 días y se renuevan solos cuando les quedan 10 días de vigencia.

Además del cifrado, cada servicio declara a qué otros servicios puede llamar. Esa lista se aplica como política de red en Kubernetes: una llamada que no figura en la lista se bloquea aunque el certificado sea válido. Por ejemplo, `reportes` puede consultar la réplica de lectura de PostgreSQL, pero no puede llamar a `pagos`.

## Acceso de personas

El acceso a producción es nominal y temporal. Nadie tiene permisos permanentes: quien necesita entrar pide acceso por la herramienta Teleport, indica el motivo y recibe una sesión de 4 horas como máximo. Las sesiones quedan grabadas y se conservan durante un año.

El ingreso requiere un segundo factor de autenticación con llave física. Los códigos por mensaje de texto no están permitidos.

## Auditoría

Toda operación que modifica un cobro o un reembolso deja un registro de auditoría con la persona o el servicio que la hizo, la fecha y el valor anterior. Esos registros se guardan en un almacenamiento de solo escritura durante 10 años, que es el plazo que exige la regulación financiera local, y ningún servicio de Atlas tiene permiso para borrarlos.

Una vez por trimestre, el equipo de seguridad revisa los accesos concedidos y los permisos de cada servicio, y elimina los que ya no se usan.
