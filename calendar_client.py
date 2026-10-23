"""
Cliente delgado sobre Google Calendar API para ofrecer y agendar horarios de
demo (GestiónVital Pro o GestiónAlmacén Pro, según la línea del lead).

Autenticación vía Service Account (sin flujo OAuth interactivo, apto para un
servicio 24/7 sin navegador): Rodrigo comparte su Google Calendar personal con
el email de la service account (ver checklist de configuración en el plan).

Ningún envío de WhatsApp pasa por acá — este módulo solo lee/escribe eventos
de calendario. Quien decide cuándo llamarlo es agent_service.py.
"""
import base64
import json
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "rvillegasburgos@gmail.com")
GOOGLE_SERVICE_ACCOUNT_JSON_B64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH")

DEMO_TZ = os.getenv("DEMO_TZ", "America/Santiago")
DEMO_DURACION_MINUTOS = int(os.getenv("DEMO_DURACION_MINUTOS", "30"))
DEMO_DIAS_HABILES = [d.strip().lower() for d in os.getenv("DEMO_DIAS_HABILES", "jueves,viernes,sabado").split(",")]
DEMO_HORA_INICIO = int(os.getenv("DEMO_HORA_INICIO", "10"))
DEMO_HORA_FIN = int(os.getenv("DEMO_HORA_FIN", "19"))
MAX_SLOTS_OFRECIDOS = 6

# Prevención de riesgos: diagnóstico de 20 min (no una demo de 30), de lunes a viernes en horario de oficina.
# Se ofrecen pocos horarios por día y repartidos en varios días, y con un margen mínimo de anticipación.
DIAG_DURACION_MINUTOS = int(os.getenv("DIAG_DURACION_MINUTOS", "20"))
DIAG_PASO_MINUTOS = int(os.getenv("DIAG_PASO_MINUTOS", "30"))
DIAG_DIAS_HABILES = [d.strip().lower() for d in os.getenv("DIAG_DIAS_HABILES", "lunes,martes,miercoles,jueves,viernes").split(",")]
DIAG_HORA_INICIO = int(os.getenv("DIAG_HORA_INICIO", "10"))
DIAG_HORA_FIN = int(os.getenv("DIAG_HORA_FIN", "18"))
DIAG_MAX_POR_DIA = int(os.getenv("DIAG_MAX_POR_DIA", "2"))
DIAG_ANTICIPACION_HORAS = int(os.getenv("DIAG_ANTICIPACION_HORAS", "4"))

# Agenda por línea de negocio. Las que no aparecen usan la demo estándar (DEMO_*).
PERFILES_AGENDA = {
    "prevencion": {
        "duracion_min": DIAG_DURACION_MINUTOS,
        "paso_min": DIAG_PASO_MINUTOS,
        "dias_habiles": DIAG_DIAS_HABILES,
        "hora_inicio": DIAG_HORA_INICIO,
        "hora_fin": DIAG_HORA_FIN,
        "max_por_dia": DIAG_MAX_POR_DIA,
        "anticipacion_horas": DIAG_ANTICIPACION_HORAS,
    },
}


def _perfil_agenda(linea):
    base = {
        "duracion_min": DEMO_DURACION_MINUTOS,
        "paso_min": None,  # None = igual a la duración
        "dias_habiles": DEMO_DIAS_HABILES,
        "hora_inicio": DEMO_HORA_INICIO,
        "hora_fin": DEMO_HORA_FIN,
        "max_por_dia": None,
        "anticipacion_horas": 0,
    }
    base.update(PERFILES_AGENDA.get(linea or "", {}))
    return base


_DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_DIAS_SEMANA_TITULO = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_SERVICIO_CACHE = None


class SlotNoDisponibleError(Exception):
    """El horario que se intentó agendar ya no está libre en el calendario."""


def _credenciales():
    if GOOGLE_SERVICE_ACCOUNT_JSON_B64:
        info = json.loads(base64.b64decode(GOOGLE_SERVICE_ACCOUNT_JSON_B64))
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    if GOOGLE_SERVICE_ACCOUNT_JSON_PATH:
        return service_account.Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON_PATH, scopes=_SCOPES)
    raise RuntimeError(
        "Faltan credenciales de Google Calendar: configura GOOGLE_SERVICE_ACCOUNT_JSON_B64 "
        "o GOOGLE_SERVICE_ACCOUNT_JSON_PATH."
    )


def _servicio():
    global _SERVICIO_CACHE
    if _SERVICIO_CACHE is None:
        _SERVICIO_CACHE = build("calendar", "v3", credentials=_credenciales(), cache_discovery=False)
    return _SERVICIO_CACHE


def _bloques_ocupados(inicio_dt, fin_dt):
    """Consulta freebusy y devuelve lista de (inicio_dt, fin_dt) ocupados, en UTC."""
    body = {
        "timeMin": inicio_dt.isoformat(),
        "timeMax": fin_dt.isoformat(),
        "items": [{"id": GOOGLE_CALENDAR_ID}],
    }
    resultado = _servicio().freebusy().query(body=body).execute()
    ocupados_raw = resultado.get("calendars", {}).get(GOOGLE_CALENDAR_ID, {}).get("busy", [])
    return [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in ocupados_raw
    ]


def _se_solapa(inicio_a, fin_a, inicio_b, fin_b):
    return inicio_a < fin_b and inicio_b < fin_a


def obtener_slots_disponibles(dias_adelante=14, duracion_min=None, linea=None):
    """
    Genera candidatos de horario dentro de los días/horario configurados
    (por defecto jueves/viernes/sábado 10:00-19:00 hora Chile) para los
    próximos `dias_adelante` días, descarta los que se solapan con eventos
    existentes, y devuelve como máximo MAX_SLOTS_OFRECIDOS.

    `linea` elige el perfil de agenda (ver PERFILES_AGENDA): duración, días, horario, tope por día
    y anticipación mínima. Sin perfil propio se usa la demo estándar.

    Devuelve list[dict]: [{"inicio_iso", "fin_iso", "etiqueta"}], o [] si hay
    cualquier error (el llamador decide qué hacer: seguir sin ofrecer horarios).
    """
    perfil = _perfil_agenda(linea)
    duracion = timedelta(minutes=duracion_min or perfil["duracion_min"])
    paso = timedelta(minutes=perfil["paso_min"] or (duracion_min or perfil["duracion_min"]))
    tz = ZoneInfo(DEMO_TZ)
    ahora = datetime.now(tz)
    primer_horario = ahora + timedelta(hours=perfil["anticipacion_horas"])
    fin_rango = ahora + timedelta(days=dias_adelante)

    try:
        ocupados = _bloques_ocupados(ahora, fin_rango)
    except Exception:
        logging.exception("No se pudo consultar disponibilidad de Google Calendar.")
        return []

    slots = []
    dia_cursor = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    while dia_cursor <= fin_rango and len(slots) < MAX_SLOTS_OFRECIDOS:
        nombre_dia = _DIAS_SEMANA[dia_cursor.weekday()]
        if nombre_dia in perfil["dias_habiles"]:
            hora_cursor = dia_cursor.replace(hour=perfil["hora_inicio"])
            fin_dia = dia_cursor.replace(hour=perfil["hora_fin"])
            del_dia = 0
            while hora_cursor + duracion <= fin_dia and len(slots) < MAX_SLOTS_OFRECIDOS:
                if perfil["max_por_dia"] and del_dia >= perfil["max_por_dia"]:
                    break
                if hora_cursor > primer_horario and not any(
                    _se_solapa(hora_cursor, hora_cursor + duracion, o_ini, o_fin) for o_ini, o_fin in ocupados
                ):
                    nombre_dia_titulo = _DIAS_SEMANA_TITULO[hora_cursor.weekday()]
                    slots.append({
                        "inicio_iso": hora_cursor.isoformat(),
                        "fin_iso": (hora_cursor + duracion).isoformat(),
                        "etiqueta": f"{nombre_dia_titulo} {hora_cursor.strftime('%d/%m %H:%M')}",
                    })
                    del_dia += 1
                    if perfil["max_por_dia"]:
                        # Con tope por día, el segundo horario se separa del primero (mañana y tarde)
                        # en lugar de quedar pegado.
                        hora_cursor += max(paso, timedelta(hours=3))
                        continue
                hora_cursor += paso
        dia_cursor += timedelta(days=1)

    return slots


def slot_esta_libre(inicio_iso, fin_iso):
    """Re-consulta freebusy solo para ese rango puntual. Se usa justo antes de
    crear el evento, para evitar doble-booking si pasó tiempo entre que se
    ofreció el slot y que Rodrigo aprobó el borrador."""
    inicio_dt = datetime.fromisoformat(inicio_iso)
    fin_dt = datetime.fromisoformat(fin_iso)
    try:
        ocupados = _bloques_ocupados(inicio_dt, fin_dt)
    except Exception:
        logging.exception("No se pudo re-validar disponibilidad de Google Calendar.")
        raise
    return not any(_se_solapa(inicio_dt, fin_dt, o_ini, o_fin) for o_ini, o_fin in ocupados)


def crear_evento(inicio_iso, fin_iso, titulo, descripcion):
    evento = {
        "summary": titulo,
        "description": descripcion,
        "start": {"dateTime": inicio_iso},
        "end": {"dateTime": fin_iso},
    }
    creado = _servicio().events().insert(calendarId=GOOGLE_CALENDAR_ID, body=evento).execute()
    return {"event_id": creado["id"], "html_link": creado.get("htmlLink", "")}


# Producto de cada línea de negocio: el evento debe llevar el nombre del producto de la demo.
PRODUCTOS = {"clinicas": "GestiónVital Pro", "almacenes": "GestiónAlmacén Pro"}
# Líneas que no venden un producto sino un servicio: el evento lleva este nombre, no "Demo <producto>".
SERVICIOS = {"prevencion": "Diagnóstico exprés (prevención de riesgos)"}


def nombre_producto(linea):
    """Producto de la línea; si no se conoce (contacto que no está en los CSV) se usa el de clínicas."""
    return PRODUCTOS.get(linea, PRODUCTOS["clinicas"])


def crear_evento_demo(inicio_iso, telefono, nombre_lead, duracion_min=None, linea=None):
    """
    Revalida que el slot siga libre y, si es así, crea el evento de demo (o el diagnóstico, en las
    líneas de servicio).
    Lanza SlotNoDisponibleError si el horario ya no está disponible: quien
    llama (agent_service.py) decide qué hacer, pero NUNCA debe enviarse una
    confirmación de WhatsApp si esto falla.
    """
    inicio_dt = datetime.fromisoformat(inicio_iso)
    duracion = duracion_min or _perfil_agenda(linea)["duracion_min"]
    fin_iso = (inicio_dt + timedelta(minutes=duracion)).isoformat()

    if not slot_esta_libre(inicio_iso, fin_iso):
        raise SlotNoDisponibleError(f"El horario {inicio_iso} ya no está disponible.")

    if linea in SERVICIOS:
        servicio = SERVICIOS[linea]
        titulo = f"{servicio} - {nombre_lead}" if nombre_lead else servicio
        descripcion = f"{servicio} agendado automáticamente vía WhatsApp. Teléfono: {telefono}."
    else:
        producto = nombre_producto(linea)
        titulo = f"Demo {producto} - {nombre_lead}" if nombre_lead else f"Demo {producto}"
        descripcion = f"Demo de {producto} agendada automáticamente vía WhatsApp. Teléfono: {telefono}."
    return crear_evento(inicio_iso, fin_iso, titulo, descripcion)
