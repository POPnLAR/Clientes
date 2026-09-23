"""
Wrapper delgado sobre la API REST gratuita de Gemini (Google AI Studio) para
redactar borradores de respuesta a mensajes entrantes de WhatsApp.

No envía nada por su cuenta: solo genera texto. El envío real siempre pasa
por aprobación humana (ver agent_service.py).
"""
import logging
import os
import re
import time

import requests
import yaml

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)
PLAYBOOK_PATH = os.getenv("PLAYBOOK_PATH", "playbook_ventas.yaml")
PLAYBOOK_ALMACENES_PATH = os.getenv("PLAYBOOK_ALMACENES_PATH", "playbook_almacenes.yaml")
# Cada línea de negocio tiene su propio producto, marca y playbook.
PLAYBOOKS_POR_LINEA = {"clinicas": PLAYBOOK_PATH, "almacenes": PLAYBOOK_ALMACENES_PATH}

# Backoff simple para respetar la capa gratuita (~15 solicitudes/min).
_ULTIMA_LLAMADA = 0.0
_INTERVALO_MINIMO_SEG = 4.5

_MARCADOR_AGENDAR_RE = re.compile(r"\[\[AGENDAR:\s*([^\]]+)\]\]\s*$")

# Cache en memoria del playbook, invalidado por mtime del archivo: así Rodrigo
# puede editar precios/objeciones sin tener que reiniciar el servicio.
_PLAYBOOK_CACHE = {}  # ruta -> {"mtime", "texto"}


_PROMPT_COMUN = """Tu tarea es redactar UN borrador de respuesta de WhatsApp para el mensaje entrante
del prospecto, en español chileno, tono cercano y profesional, breve (máximo 3-4 oraciones). Usa
EXCLUSIVAMENTE los datos de la sección "--- Playbook de ventas ---" de abajo: nunca inventes un
precio, plan, promoción, plazo o promesa que no esté ahí. Lee bien lo que dijo el prospecto y
respóndele a eso: si solo agradece, avisa que ya tiene algo o dice que lo verá, responde con
naturalidad y sin presionar, en vez de repetir un discurso de venta. Si el mensaje suena a reclamo
fuerte, confusión total, o pide hablar con un humano, responde reconociendo eso y ofreciendo que
Rodrigo le escriba directamente, sin intentar resolverlo tú solo. Nunca reveles que eres una IA a
menos que te pregunten explícitamente.

Si en la sección "--- Horarios disponibles para demo ---" hay horarios listados y el prospecto
CONFIRMA explícitamente e inequívocamente uno de esos horarios (por ejemplo "el jueves a las 10
me sirve"), agrega como última línea de tu respuesta, en su propia línea, exactamente:
[[AGENDAR: <inicio_iso_exacto_tal_como_aparece_en_la_lista>]]
No agregues esa línea si el prospecto no confirmó un horario específico de la lista, y nunca
inventes un horario que no esté en la lista.

Devuelve SOLO el texto del mensaje de WhatsApp (más la línea [[AGENDAR: ...]] si corresponde),
sin comillas ni explicaciones adicionales."""

_PROMPT_CLINICAS = (
    "Eres el asistente de ventas de Rodrigo, dueño de GestiónVital Pro, una empresa chilena que "
    "ofrece una app de gestión para clínicas y centros de estética (agenda, fichas de pacientes, "
    "inventario, etc.). " + _PROMPT_COMUN
)

_PROMPT_ALMACENES = (
    "Eres el asistente de ventas de Rodrigo, dueño de GestiónAlmacén Pro (https://gestionalmacenpro.cl), "
    "una app chilena para dueños de almacenes, minimarkets, botillerías y emporios de barrio que "
    "permite controlar el stock, ver las ventas diarias y ordenar las cuentas desde el celular. "
    "IMPORTANTE: este prospecto es un negocio de barrio, NO una clínica. GestiónVital Pro es OTRO "
    "producto (para clínicas estéticas): nunca lo menciones ni uses sus planes, precios, prueba "
    "gratis, registro o características. Si te preguntan algo que no figura en el playbook (precios, "
    "planes, prueba gratis, migración de datos, contrato...), no lo inventes ni lo supongas: di con "
    "honestidad que Rodrigo se lo detalla en una demo corta por WhatsApp y ofrece agendarla. "
    + _PROMPT_COMUN
)

SYSTEM_PROMPT = _PROMPT_CLINICAS  # compatibilidad


def _prompt_de_sistema(linea):
    return _PROMPT_ALMACENES if linea == "almacenes" else _PROMPT_CLINICAS


def _esperar_rate_limit():
    global _ULTIMA_LLAMADA
    transcurrido = time.time() - _ULTIMA_LLAMADA
    if transcurrido < _INTERVALO_MINIMO_SEG:
        time.sleep(_INTERVALO_MINIMO_SEG - transcurrido)
    _ULTIMA_LLAMADA = time.time()


def _cargar_playbook(linea="clinicas"):
    """
    Lee el playbook de la línea (playbook_ventas.yaml o playbook_almacenes.yaml) y cachea el texto
    en memoria, recargando solo si el archivo cambió de mtime. Si el archivo no existe o es
    inválido, se loguea el error y se devuelve texto vacío (el agente sigue funcionando, solo
    pierde el contexto de precios/promos hasta que se arregle el archivo).
    """
    ruta = PLAYBOOKS_POR_LINEA.get(linea or "clinicas", PLAYBOOK_PATH)
    try:
        mtime = os.path.getmtime(ruta)
    except OSError:
        logging.error("No se encontró el playbook de ventas en %s.", ruta)
        return ""

    cache = _PLAYBOOK_CACHE.get(ruta)
    if cache and cache["mtime"] == mtime:
        return cache["texto"]

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = yaml.safe_load(f) or {}
        texto = _formatear_playbook_para_prompt(datos)
        _PLAYBOOK_CACHE[ruta] = {"mtime": mtime, "texto": texto}
        return texto
    except Exception:
        logging.exception("No se pudo leer/parsear %s.", ruta)
        return cache["texto"] if cache else ""


def _formatear_playbook_para_prompt(playbook):
    lineas = []

    empresa = playbook.get("empresa", {})
    if empresa:
        if empresa.get("nombre"):
            sitio = f" ({empresa['sitio_web']})" if empresa.get("sitio_web") else ""
            lineas.append(f"Producto: {empresa['nombre']}{sitio}.")
        if empresa.get("descripcion"):
            lineas.append(str(empresa["descripcion"]).strip())
        if empresa.get("trial_dias"):
            lineas.append(f"Trial gratis: {empresa.get('trial_dias')} días, registro en {empresa.get('registro_trial', '')}.")
        if empresa.get("sin_contrato_permanencia"):
            lineas.append("Sin contrato de permanencia, cancela cuando quiera.")
        if empresa.get("migracion_datos_gratis"):
            lineas.append("Migración de datos desde el sistema anterior incluida sin costo.")

    beneficios = playbook.get("beneficios", [])
    if beneficios:
        lineas.append("\nQué valoran los dueños de negocios que usan la app:")
        lineas += [f"- {b}" for b in beneficios]

    no_disponible = playbook.get("no_disponible", [])
    if no_disponible:
        lineas.append(
            "\nInformación NO disponible (no la afirmes ni la inventes; si preguntan, di que Rodrigo se "
            "la detalla en la demo): " + ", ".join(no_disponible) + "."
        )

    planes = playbook.get("planes", [])
    if planes:
        lineas.append("\nPlanes:")
        for p in planes:
            precio = f"${p.get('precio_mensual_clp', 0):,}".replace(",", ".") + "/mes + IVA"
            nota = f" ({p['nota_oferta']})" if p.get("nota_oferta") else ""
            incluye = ", ".join(p.get("incluye", []))
            lineas.append(f"- {p.get('nombre')} [{p.get('id')}]: {precio}{nota}. Incluye: {incluye}.")

    promos = playbook.get("promociones", [])
    if promos:
        lineas.append("\nPromociones activas:")
        for promo in promos:
            precio = f"${promo.get('precio_mensual_clp', 0):,}".replace(",", ".") + "/mes + IVA"
            lineas.append(f"- {promo.get('nombre')} [{promo.get('id')}]: {precio}. {promo.get('condiciones', '')}")

    reglas = playbook.get("reglas_recomendacion", [])
    if reglas:
        lineas.append("\nGuía para recomendar plan según el lead:")
        for r in reglas:
            lineas.append(f"- Si {r.get('condicion')} -> sugerir '{r.get('plan_sugerido')}'.")

    objeciones = playbook.get("objeciones", [])
    if objeciones:
        lineas.append("\nRespuestas sugeridas a objeciones frecuentes:")
        for o in objeciones:
            lineas.append(f"- \"{o.get('objecion')}\" -> {o.get('respuesta_sugerida', '').strip()}")

    demo = playbook.get("demo", {})
    if demo:
        texto_demo = f"\nDemo: {demo.get('modalidad', '')}"
        if demo.get("dias_disponibles"):
            dias = ", ".join(demo.get("dias_disponibles", []))
            texto_demo += f" Días disponibles: {dias}, de {demo.get('horario_inicio')} a {demo.get('horario_fin')}."
        if demo.get("duracion_minutos"):
            texto_demo += f" Duración: {demo.get('duracion_minutos')} minutos."
        lineas.append(texto_demo)

    return "\n".join(lineas)


def _formatear_slots_para_prompt(slots_disponibles):
    return "\n".join(f"- {s['inicio_iso']} ({s.get('etiqueta', s['inicio_iso'])})" for s in slots_disponibles)


def _extraer_marcador_agendar(texto_generado):
    """
    Busca la línea final [[AGENDAR: <inicio_iso>]] en el texto devuelto por
    Gemini. Devuelve (texto_visible_sin_el_marcador, accion_o_None), donde
    accion es {"tipo": "agendar_cita", "inicio_iso": str} si hubo match.

    La validación de que ese inicio_iso corresponda a un slot REAL ofrecido
    (y no una alucinación) la hace agent_service.py, que es quien conoce la
    lista real de slots que se ofrecieron.
    """
    match = _MARCADOR_AGENDAR_RE.search(texto_generado)
    if not match:
        return texto_generado, None
    inicio_iso = match.group(1).strip()
    texto_visible = texto_generado[: match.start()].rstrip()
    return texto_visible, {"tipo": "agendar_cita", "inicio_iso": inicio_iso}


def generar_borrador(historial, contexto_lead, mensaje_entrante, slots_disponibles=None,
                     instruccion_operador=None, borrador_anterior=None, linea=None):
    """
    linea: "clinicas" | "almacenes" | None. Elige el producto, la marca y el playbook con los que se
    responde (un almacén no debe recibir el discurso ni los precios de GestiónVital). Si no se conoce
    (contacto que no está en los CSV), se usa el de clínicas.

    instruccion_operador / borrador_anterior: solo al regenerar. El operador puede pedir un ajuste
    ("más corto", "ya tienen asesoría, no insistas") y se le muestra al modelo el borrador que no
    convenció para que no lo repita.

    historial: lista de dicts [{"direccion": "in"|"out", "texto": str}, ...] (más antiguos primero)
    contexto_lead: dict con campos como Evento, Ubicacion, Estado, Dia_Secuencia (puede venir vacío
                   si el lead no se encontró en el CSV por teléfono)
    mensaje_entrante: texto del último mensaje del prospecto que dispara este borrador
    slots_disponibles: lista opcional de dicts [{"inicio_iso", "fin_iso", "etiqueta"}, ...] con
                        horarios de demo realmente libres, para que el agente pueda ofrecerlos y
                        detectar si el prospecto confirma uno.

    Devuelve (texto_borrador, accion_o_None). accion es {"tipo": "agendar_cita", "inicio_iso": str}
    si el prospecto confirmó un horario, o None en cualquier otro caso (incluido el fallback, para
    que el flujo de aprobación humana nunca se bloquee por completo).
    """
    if not GEMINI_API_KEY:
        logging.error("GEMINI_API_KEY no configurado; devolviendo borrador de fallback.")
        return _borrador_fallback(mensaje_entrante), None

    playbook_txt = _cargar_playbook(linea)
    contexto_txt = "\n".join(f"{k}: {v}" for k, v in (contexto_lead or {}).items() if v)
    historial_txt = "\n".join(
        f"{'Prospecto' if m['direccion'] == 'in' else 'Rodrigo'}: {m['texto']}" for m in historial
    )
    slots_txt = _formatear_slots_para_prompt(slots_disponibles) if slots_disponibles else ""

    prompt = (
        f"{_prompt_de_sistema(linea)}\n\n"
        f"--- Playbook de ventas ---\n{playbook_txt or '(playbook no disponible)'}\n\n"
        f"--- Horarios disponibles para demo ---\n{slots_txt or '(sin horarios disponibles por ahora)'}\n\n"
        f"--- Contexto del lead ---\n{contexto_txt or '(sin datos del lead en la base)'}\n\n"
        f"--- Historial reciente ---\n{historial_txt or '(sin historial previo)'}\n\n"
        f"--- Último mensaje del prospecto ---\n{mensaje_entrante}\n\n"
        f"{_seccion_regeneracion(instruccion_operador, borrador_anterior)}"
        f"Redacta el borrador de respuesta:"
    )

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    if instruccion_operador or borrador_anterior:
        payload["generationConfig"] = {"temperature": 0.9}  # más variedad que la primera vez

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
            return _borrador_fallback(mensaje_entrante), None

        data = res.json()
        candidatos = data.get("candidates", [])
        if not candidatos:
            logging.error("Gemini no devolvió candidatos: %s", data)
            return _borrador_fallback(mensaje_entrante), None

        partes = candidatos[0].get("content", {}).get("parts", [])
        texto = "".join(p.get("text", "") for p in partes).strip()
        if not texto:
            return _borrador_fallback(mensaje_entrante), None
        return _extraer_marcador_agendar(texto)
    except Exception:
        logging.exception("Excepción al llamar a Gemini API.")
        return _borrador_fallback(mensaje_entrante), None


def _seccion_regeneracion(instruccion_operador, borrador_anterior):
    partes = []
    if borrador_anterior:
        partes.append(
            "--- Borrador anterior (el operador NO quedó conforme: no lo repitas, escribe uno distinto "
            f"que responda mejor al último mensaje del prospecto) ---\n{borrador_anterior}\n\n"
        )
    if instruccion_operador and instruccion_operador.strip():
        partes.append(
            "--- Indicación del operador para esta respuesta (síguela; sigue mandando el playbook para "
            f"precios y condiciones) ---\n{instruccion_operador.strip()}\n\n"
        )
    return "".join(partes)


def _borrador_fallback(mensaje_entrante):
    return (
        "¡Hola! Gracias por escribirme 🙌 Estoy revisando tu mensaje y te respondo apenas pueda "
        "con más detalle. Cualquier duda urgente, quedo atento."
    )
