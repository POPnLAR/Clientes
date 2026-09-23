"""
Filtro determinístico (cero tokens) que decide si un mensaje entrante merece una
llamada a Gemini. Se ejecuta en el webhook ANTES de generar el borrador.

Las reglas y sus umbrales se pueden ajustar en la sección `filtros:` de
playbook_ventas.yaml (se recarga solo al cambiar el archivo, sin reiniciar el
servicio). Si la sección falta o es inválida se usan los valores por defecto.
"""
import logging
import os
import re
import unicodedata

import yaml

PLAYBOOK_PATH = os.getenv("PLAYBOOK_PATH", "playbook_ventas.yaml")

_DEFAULTS = {
    "activo": True,
    # Solo responder a números que estén en los CSV de leads o con los que ya
    # hayamos conversado (tengan al menos un mensaje saliente nuestro).
    "ignorar_desconocidos": True,
    # Los contactos @lid no traen teléfono, así que nunca coinciden con un lead
    # (llegan por links wa.me). Con true se responden aunque sean "desconocidos".
    "responder_lid": True,
    # Espera N segundos y, si el mismo contacto mandó otro mensaje entre medio,
    # se responde solo al último (una llamada en vez de una por cada mensaje).
    "debounce_segundos": 6,
    # Más de N mensajes entrantes del mismo número en 60 min = bot/spam en bucle.
    "max_entrantes_por_hora": 8,
    # El mismo texto más de N veces en 10 min = bot repitiendo.
    "repetidos_max": 2,
    # Patrones (regex, sin acentos y en minúsculas) de autorespuestas, saludos
    # de bienvenida y menús de bots. Se suman a los propios del YAML.
    "patrones_bot": [
        r"mensaje automatico",
        r"respuesta automatica",
        r"auto ?respuesta",
        r"fuera de (nuestro )?horario",
        r"nuestro horario de atencion",
        # Un bot habla en primera persona plural ("contactarnos", "escribirnos"); una persona que
        # responde dice "gracias por escribir" a secas, y eso NO debe filtrarse.
        r"gracias por (contactarnos|escribirnos|preferirnos|comunicarte con (nosotros|\w+)|(contactar|escribir) a )",
        r"en breve (te|le|nos)\b",
        r"(te|le) (responderemos|contactaremos|atenderemos)\b",
        r"nos pondremos en contacto",
        r"\bbienvenid[oa]s?\b",
        r"que (necesitas|buscas|deseas)\b",
        r"en que (te|le) (puedo|podemos|podria|podriamos) ayudar",
        r"como (te|le) (puedo|podemos) ayudar",
        r"selecciona (una )?opcion",
        r"escribe (el numero|la palabra|menu)\b",
        r"\bdigita\b",
        r"\bmarca (el )?[1-9]\b",
        r"no (respondas|contestes) a este mensaje",
        r"asistente virtual",
        r"\bchatbot\b",
        # Presentaciones de asistentes de negocios ("soy la asistente de X, me encargo de...")
        r"\bsoy (la|el|una?) (asistente|secretaria virtual|bot)\b",
        r"en lo que (pueda|puedo|podamos|podria|podriamos) ayudar",
        r"me encargo de (ayudar|atender|gestionar)",
        r"(puedo|podemos) (gestionar|agendar|coordinar) (las |tus |sus )?(reservas|citas)",
        r"\bque gusto (saludarte|saludarle|saludarlos)\b",
        # Bots de derivación ("he pasado tu mensaje a nuestro equipo...")
        r"\b(he|hemos) (pasado|derivado|enviado|reenviado|transferido) (tu|su) (mensaje|consulta|solicitud)",
        r"\b(pase|derive|envie|reenvie) (tu|su) (mensaje|consulta|solicitud) a (nuestro|el|la) ",
        r"(tu|su) (mensaje|consulta|solicitud) (ha sido|fue) (recibid|derivad|enviad|transferid)",
        r"para que puedan revisarl[oa]",
        r"(nuestro )?equipo (revisara|se pondra en contacto|te contactara|le contactara|te respondera|le respondera)",
        r"un (ejecutivo|asesor|agente) (te|le) (contactara|respondera|atendera|escribira)",
    ],
    # Mensajes que cierran la conversación y no necesitan respuesta.
    "cierres": [
        "ok", "okey", "oka", "okis", "gracias", "muchas gracias", "mil gracias",
        "listo", "perfecto", "dale", "vale", "genial", "excelente", "de nada",
        "igualmente", "saludos", "chao", "chau", "adios", "hasta luego",
    ],
}

_CACHE = {"mtime": None, "cfg": None}


def _sin_acentos(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def cargar_config():
    """Devuelve la config efectiva (defaults + sección `filtros` del playbook)."""
    try:
        mtime = os.path.getmtime(PLAYBOOK_PATH)
    except OSError:
        return _DEFAULTS
    if _CACHE["mtime"] == mtime and _CACHE["cfg"] is not None:
        return _CACHE["cfg"]

    cfg = dict(_DEFAULTS)
    try:
        with open(PLAYBOOK_PATH, "r", encoding="utf-8") as f:
            propios = (yaml.safe_load(f) or {}).get("filtros") or {}
        for clave, valor in propios.items():
            if clave in ("patrones_bot", "cierres"):
                cfg[clave] = list(_DEFAULTS[clave]) + [str(v) for v in (valor or [])]
            elif clave in _DEFAULTS:
                cfg[clave] = valor
    except Exception:
        logging.exception("No se pudo leer la sección 'filtros' de %s; se usan los defaults.", PLAYBOOK_PATH)
        cfg = _DEFAULTS
    _CACHE.update({"mtime": mtime, "cfg": cfg})
    return cfg


def es_mensaje_de_bot(texto, cfg):
    """Autorespuestas, bienvenidas ('¿qué necesitas?') y menús numerados."""
    t = _sin_acentos(texto)
    lineas_menu = sum(
        1 for linea in t.splitlines() if re.match(r"^\s*\(?[1-9][\).\-:]\s+\S", linea)
    )
    if lineas_menu >= 2:
        return True
    t = re.sub(r"\s+", " ", t)
    return any(re.search(p, t) for p in cfg["patrones_bot"])


def es_cierre(texto, cfg):
    """'ok', 'gracias', un emoji suelto... (mensajes sin letras ni cifras cuentan)."""
    t = re.sub(r"[^a-z0-9ñ ]", " ", _sin_acentos(texto))
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return True  # solo emojis / signos
    if t in cfg["cierres"]:
        return True
    palabras = t.split()
    if len(palabras) > 4:
        return False
    validas = {p for c in cfg["cierres"] for p in c.split()}
    return all(p in validas for p in palabras)


_OFERTA_HORARIO = re.compile(r"\d{1,2}[:.]\d{2}|jueves|viernes|sabado|demo|horario", re.IGNORECASE)


def nuestro_ultimo_mensaje_ofrecia_horario(ultimo_saliente):
    return bool(ultimo_saliente and _OFERTA_HORARIO.search(_sin_acentos(ultimo_saliente)))


def evaluar(texto, *, cfg, es_conocido, es_lid, esperando_confirmacion,
            entrantes_ultima_hora, repeticiones_recientes):
    """
    Devuelve None si el mensaje debe ir a Gemini, o el motivo (str) por el que se
    descarta. `entrantes_ultima_hora` y `repeticiones_recientes` ya incluyen el
    mensaje actual.
    """
    if not cfg["activo"]:
        return None
    if cfg["ignorar_desconocidos"] and not es_conocido and not (es_lid and cfg["responder_lid"]):
        return "desconocido"
    if es_mensaje_de_bot(texto, cfg):
        return "bot"
    if es_cierre(texto, cfg) and not esperando_confirmacion:
        return "cierre"
    if repeticiones_recientes > cfg["repetidos_max"]:
        return "repetido"
    if entrantes_ultima_hora > cfg["max_entrantes_por_hora"]:
        return "flood"
    return None
