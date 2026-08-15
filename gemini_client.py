"""
Wrapper delgado sobre la API REST gratuita de Gemini (Google AI Studio) para
redactar borradores de respuesta a mensajes entrantes de WhatsApp.

No envía nada por su cuenta: solo genera texto. El envío real siempre pasa
por aprobación humana (ver agent_service.py).
"""
import logging
import os
import time

import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

# Backoff simple para respetar la capa gratuita (~15 solicitudes/min).
_ULTIMA_LLAMADA = 0.0
_INTERVALO_MINIMO_SEG = 4.5


SYSTEM_PROMPT = """Eres el asistente de ventas de Rodrigo, dueño de GestiónVital Pro, una empresa
chilena que ofrece una app de gestión (para clínicas estéticas o almacenes de barrio, según el
contexto del lead). Tu tarea es redactar UN borrador de respuesta de WhatsApp para el mensaje
entrante del prospecto, en español chileno, tono cercano y profesional, breve (máximo 3-4
oraciones), sin inventar precios ni promesas que no se te dieron como contexto. Si el mensaje
entrante suena a reclamo fuerte, confusión total, o pide hablar con un humano, responde
reconociendo eso y ofreciendo que Rodrigo le escriba directamente, sin intentar resolverlo tú
solo. Nunca reveles que eres una IA a menos que te pregunten explícitamente. Devuelve SOLO el
texto del mensaje de WhatsApp, sin comillas ni explicaciones adicionales."""


def _esperar_rate_limit():
    global _ULTIMA_LLAMADA
    transcurrido = time.time() - _ULTIMA_LLAMADA
    if transcurrido < _INTERVALO_MINIMO_SEG:
        time.sleep(_INTERVALO_MINIMO_SEG - transcurrido)
    _ULTIMA_LLAMADA = time.time()


def generar_borrador(historial, contexto_lead, mensaje_entrante):
    """
    historial: lista de dicts [{"direccion": "in"|"out", "texto": str}, ...] (más antiguos primero)
    contexto_lead: dict con campos como Evento, Ubicacion, Estado, Dia_Secuencia (puede venir vacío
                   si el lead no se encontró en el CSV por teléfono)
    mensaje_entrante: texto del último mensaje del prospecto que dispara este borrador

    Devuelve el texto del borrador, o una respuesta de fallback si Gemini no está configurado
    o falla (para que el flujo de aprobación humana nunca se bloquee por completo).
    """
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY no configurado; devolviendo borrador de fallback.")
        return _borrador_fallback(mensaje_entrante)

    contexto_txt = "\n".join(f"{k}: {v}" for k, v in (contexto_lead or {}).items() if v)
    historial_txt = "\n".join(
        f"{'Prospecto' if m['direccion'] == 'in' else 'Rodrigo'}: {m['texto']}" for m in historial
    )

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"--- Contexto del lead ---\n{contexto_txt or '(sin datos del lead en la base)'}\n\n"
        f"--- Historial reciente ---\n{historial_txt or '(sin historial previo)'}\n\n"
        f"--- Último mensaje del prospecto ---\n{mensaje_entrante}\n\n"
        f"Redacta el borrador de respuesta:"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        _esperar_rate_limit()
        res = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=30,
        )
        if res.status_code != 200:
            logging.error("Gemini API error: HTTP %s - %s", res.status_code, res.text[:500])
            return _borrador_fallback(mensaje_entrante)

        data = res.json()
        candidatos = data.get("candidates", [])
        if not candidatos:
            logging.error("Gemini no devolvió candidatos: %s", data)
            return _borrador_fallback(mensaje_entrante)

        partes = candidatos[0].get("content", {}).get("parts", [])
        texto = "".join(p.get("text", "") for p in partes).strip()
        return texto or _borrador_fallback(mensaje_entrante)
    except Exception:
        logging.exception("Excepción al llamar a Gemini API.")
        return _borrador_fallback(mensaje_entrante)


def _borrador_fallback(mensaje_entrante):
    return (
        "¡Hola! Gracias por escribirme 🙌 Estoy revisando tu mensaje y te respondo apenas pueda "
        "con más detalle. Cualquier duda urgente, quedo atento."
    )
