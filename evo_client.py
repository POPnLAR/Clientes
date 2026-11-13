"""
Cliente compartido para Evolution API (WhatsApp).
Usado por worker.py, worker_almacenes.py y agent_service.py para no duplicar
la lógica de envío, normalización de teléfono y chequeo de estado de sesión.
"""
import logging
import random
import time

from urllib.parse import urljoin, urlparse

import requests


def _mismo_sitio(url_a, url_b):
    """True si ambas URLs apuntan al mismo host, ignorando un 'www.' inicial."""
    def host(u):
        h = (urlparse(u).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    return bool(host(url_a)) and host(url_a) == host(url_b)


def post_sin_perder_metodo(url, max_saltos=3, **kwargs):
    """
    requests.post que sigue redirecciones SIN convertir el POST en GET.

    Por defecto `requests` (como los navegadores) repite un 301/302 como GET y sin
    cuerpo; contra Evolution eso termina en un 404 "Cannot GET /message/sendText/..."
    que parece un problema de la API cuando en realidad es nginx redirigiendo el
    dominio (p. ej. gestionvitalpro.cl -> www.gestionvitalpro.cl).

    Solo se sigue la redirección si lleva al mismo sitio (mismo host, con o sin
    'www.'), para no reenviar el token a un tercero. Si no, se devuelve la
    respuesta 3xx tal cual y el llamador la reporta como error.
    """
    res = requests.post(url, allow_redirects=False, **kwargs)
    saltos = 0
    while res.status_code in (301, 302, 307, 308) and saltos < max_saltos:
        destino = res.headers.get("Location")
        if not destino:
            break
        destino = urljoin(url, destino)
        if not _mismo_sitio(url, destino):
            logging.error("Redirección a otro sitio ignorada: %s -> %s", url, destino)
            break
        logging.warning(
            "La URL configurada redirige (HTTP %s): %s -> %s. Conviene actualizar la variable de entorno.",
            res.status_code, url, destino,
        )
        url = destino
        res = requests.post(url, allow_redirects=False, **kwargs)
        saltos += 1
    return res


def normalizar_telefono_chile(raw):
    """
    Normaliza distintos formatos de teléfono chileno a un formato consistente.
    Preferimos devolver '56XXXXXXXXX' cuando es posible.
    """
    digits = "".join(filter(str.isdigit, str(raw)))
    if not digits:
        return ""

    if digits.startswith("56") and len(digits) >= 11:
        return digits

    while digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 9 and digits.startswith("9"):
        return "56" + digits

    if len(digits) == 9 and not digits.startswith("9"):
        return "56" + digits

    if len(digits) > 9:
        ultimos = digits[-9:]
        if len(ultimos) == 9:
            return "56" + ultimos

    return digits


def verificar_estado_conexion(base_url, instance, token, timeout=15):
    """
    Consulta el estado de la sesión de WhatsApp en Evolution API.
    Devuelve "open", "close" o "unknown" (nunca lanza excepción hacia el caller).

    NOTA: el endpoint/shape exacto puede variar según la versión de Evolution API
    instalada. Se hace parsing tolerante y se loguea el payload crudo para poder
    ajustar rápido si el shape real difiere de lo esperado.
    """
    if not base_url or not instance or not token:
        return "unknown"

    url = f"{base_url.strip().rstrip('/')}/instance/connectionState/{instance}"
    headers = {"apikey": token}
    try:
        res = requests.get(url, headers=headers, timeout=timeout)
        if res.status_code != 200:
            logging.warning(
                "Estado de conexión: HTTP %s al consultar %s. Respuesta: %s",
                res.status_code, url, res.text[:500],
            )
            return "unknown"

        data = res.json()
        # Formas típicas observadas en distintas versiones de Evolution API:
        #   {"instance": {"state": "open"}}
        #   {"state": "open"}
        estado = None
        if isinstance(data, dict):
            if isinstance(data.get("instance"), dict):
                estado = data["instance"].get("state")
            if not estado:
                estado = data.get("state")

        if estado in ("open", "close", "connecting"):
            return "open" if estado == "open" else "close"

        logging.warning("Estado de conexión con shape inesperado: %s", data)
        return "unknown"
    except Exception:
        logging.exception("Error al consultar estado de conexión de Evolution API.")
        return "unknown"


def enviar_mensaje_texto(base_url, token, instance, numero, mensaje, simular_escritura=True):
    """
    Envía un mensaje de texto por WhatsApp vía Evolution API, simulando
    presencia "escribiendo..." con una demora aleatoria antes del envío.
    Devuelve True/False según si Evolution aceptó el mensaje.

    Payloads en formato Evolution API v2 (verificado contra v2.3.7): a diferencia
    de v1, `/chat/sendPresence` exige `delay` en el body y `/message/sendText` ya
    no acepta el `textMessage.text` anidado de v1 (responde 400 "instance
    requires property text") — hay que mandar `text` plano.
    """
    if not mensaje or len(mensaje.strip()) < 10:
        return False
    if not base_url or not token or not instance:
        logging.error("Evolution API no configurado (base_url/token/instance faltante).")
        return False

    base = base_url.strip().rstrip("/")
    headers = {"Content-Type": "application/json", "apikey": token}
    try:
        post_sin_perder_metodo(
            f"{base}/chat/sendPresence/{instance}",
            json={"number": numero, "presence": "composing", "delay": 1000},
            headers=headers,
            timeout=10,
        )

        if simular_escritura:
            time.sleep(random.randint(15, 35))

        payload = {"number": numero, "text": mensaje, "delay": 2000}
        res = post_sin_perder_metodo(
            f"{base}/message/sendText/{instance}", json=payload, headers=headers, timeout=20
        )
        if res.status_code not in (200, 201):
            logging.error(
                "Error al enviar mensaje. HTTP %s, respuesta: %s", res.status_code, res.text[:500]
            )
        return res.status_code in (200, 201)
    except Exception:
        logging.exception("Excepción al enviar mensaje de texto por WhatsApp.")
        return False


def enviar_alerta_whatsapp(base_url, token, instance, numero_operador, texto):
    """
    Intento best-effort de auto-alertarse por WhatsApp cuando algo se degrada
    (fallos repetidos con sesión aún 'open', errores de SerpAPI, etc).
    No usar para avisar caída total de sesión: en ese caso este envío también
    fallará (con razón), por eso es "best-effort" y nunca lanza excepción.
    """
    if not numero_operador:
        return False
    try:
        return enviar_mensaje_texto(
            base_url, token, instance, numero_operador, texto, simular_escritura=False
        )
    except Exception:
        logging.exception("Fallo al intentar enviar alerta por WhatsApp.")
        return False


def es_movil_chileno(numero):
    """True solo para celulares chilenos (569XXXXXXXX). Fijos, 600/800 y otros no reciben WhatsApp."""
    digits = "".join(filter(str.isdigit, str(numero)))
    return len(digits) == 11 and digits.startswith("569")


def verificar_whatsapp(base_url, token, instance, numeros, timeout=30):
    """
    Consulta a Evolution qué números tienen WhatsApp. Devuelve {numero: bool}.
    Si la consulta falla devuelve {} (el llamador debe tratar "sin dato" como
    "permitir": una caída de Evolution no debe frenar la prospección).
    """
    numeros = [n for n in numeros if n]
    if not numeros or not base_url or not token or not instance:
        return {}
    try:
        res = post_sin_perder_metodo(
            f"{base_url.strip().rstrip('/')}/chat/whatsappNumbers/{instance}",
            json={"numbers": numeros},
            headers={"Content-Type": "application/json", "apikey": token},
            timeout=timeout,
        )
        if res.status_code not in (200, 201):
            logging.warning("Verificación de WhatsApp: HTTP %s %s", res.status_code, res.text[:200])
            return {}
        return {
            "".join(filter(str.isdigit, str(r.get("number", "")))): bool(r.get("exists"))
            for r in res.json()
            if isinstance(r, dict)
        }
    except Exception:
        logging.exception("No se pudo verificar qué números tienen WhatsApp.")
        return {}
