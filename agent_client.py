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


def obtener_telefonos_que_respondieron(dias=90, timeout=15):
    """
    Devuelve (conjunto_de_claves_de_telefono, estado) con estado en:
      "ok"            -> consulta exitosa
      "no_configurado"-> faltan AGENT_SERVICE_URL/AGENT_SERVICE_TOKEN (comportamiento anterior)
      "error"         -> el agente no respondió (el llamador decide cómo actuar)
    """
    url = (os.getenv("AGENT_SERVICE_URL") or "").strip().rstrip("/")
    token = os.getenv("AGENT_SERVICE_TOKEN") or ""
    if not url:
        logging.warning("AGENT_SERVICE_URL no configurado: no se pausa la secuencia de quienes responden.")
        return set(), "no_configurado"
    try:
        res = requests.get(
            f"{url}/replied-phones",
            params={"dias": dias},
            headers={"x-agent-token": token} if token else {},
            timeout=timeout,
        )
        if res.status_code != 200:
            logging.error("Agente respondió HTTP %s al consultar quién respondió: %s", res.status_code, res.text[:200])
            return set(), "error"
        return {clave_telefono(t) for t in res.json().get("telefonos", [])}, "ok"
    except Exception:
        logging.exception("No se pudo consultar al agente quién respondió.")
        return set(), "error"
