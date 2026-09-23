"""
Cliente de los workers (GitHub Actions) hacia el agente conversacional (VPS).
Sirve para saber qué leads ya respondieron por WhatsApp y pausar su secuencia
automática: quien está conversando no debe seguir recibiendo "disculpen si insisto".
"""
import logging
import os

import requests


def clave_telefono(tel):
    """Últimos 9 dígitos: forma común para comparar teléfonos de distinto formato."""
    return "".join(filter(str.isdigit, str(tel)))[-9:]


def obtener_respuestas(dias=90, timeout=15):
    """
    Consulta al agente quién respondió. Devuelve {"humanos": set, "bots": set, "estado": str} con
    claves de teléfono (últimos 9 dígitos). estado:
      "ok"             -> consulta exitosa
      "no_configurado" -> faltan AGENT_SERVICE_URL/AGENT_SERVICE_TOKEN (comportamiento anterior)
      "error"          -> el agente no respondió (el llamador decide cómo actuar)
    `humanos` respondieron de verdad (se pausa su secuencia); `bots` solo respondió un bot o una
    autorespuesta (se cierra su secuencia: nadie la va a leer).
    """
    vacio = {"humanos": set(), "bots": set()}
    url = (os.getenv("AGENT_SERVICE_URL") or "").strip().rstrip("/")
    token = os.getenv("AGENT_SERVICE_TOKEN") or ""
    if not url:
        logging.warning("AGENT_SERVICE_URL no configurado: no se pausa la secuencia de quienes responden.")
        return {**vacio, "estado": "no_configurado"}
    try:
        res = requests.get(
            f"{url}/replied-phones",
            params={"dias": dias},
            headers={"x-agent-token": token} if token else {},
            timeout=timeout,
        )
        if res.status_code != 200:
            logging.error("Agente respondió HTTP %s al consultar quién respondió: %s", res.status_code, res.text[:200])
            return {**vacio, "estado": "error"}
        datos = res.json()
        return {
            "humanos": {clave_telefono(t) for t in datos.get("telefonos", [])},
            "bots": {clave_telefono(t) for t in datos.get("bots", [])},
            "estado": "ok",
        }
    except Exception:
        logging.exception("No se pudo consultar al agente quién respondió.")
        return {**vacio, "estado": "error"}


def obtener_telefonos_que_respondieron(dias=90, timeout=15):
    """Compatibilidad: (conjunto de quienes respondieron, estado)."""
    r = obtener_respuestas(dias, timeout)
    return r["humanos"], r["estado"]
