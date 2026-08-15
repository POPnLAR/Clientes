"""
Cliente compartido para Evolution API (WhatsApp).
Usado por worker.py, worker_almacenes.py y agent_service.py para no duplicar
la lógica de envío, normalización de teléfono y chequeo de estado de sesión.
"""
import logging
import random
import time

import requests


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
    """
    if not mensaje or len(mensaje.strip()) < 10:
        return False
    if not base_url or not token or not instance:
        logging.error("Evolution API no configurado (base_url/token/instance faltante).")
        return False

    base = base_url.strip().rstrip("/")
    headers = {"Content-Type": "application/json", "apikey": token}
    try:
        requests.post(
            f"{base}/chat/sendPresence/{instance}",
            json={"number": numero, "presence": "composing"},
            headers=headers,
            timeout=10,
        )

        if simular_escritura:
            time.sleep(random.randint(15, 35))

        payload = {
            "number": numero,
            "options": {"delay": 2000, "presence": "composing"},
            "textMessage": {"text": mensaje},
        }
        res = requests.post(
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
