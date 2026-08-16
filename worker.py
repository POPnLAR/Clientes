import pandas as pd
import requests
import os
import random
import sys
import time
import unicodedata
import re
import json
import math
import calendar
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import logging

from evo_client import (
    normalizar_telefono_chile as _normalizar_telefono_chile,
    verificar_estado_conexion,
    enviar_mensaje_texto as _evo_enviar_mensaje_texto,
    enviar_alerta_whatsapp,
)

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
NUMERO_OPERADOR = os.getenv("NUMERO_OPERADOR", "")
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"
ARCHIVO_ALERTA = "alert_status.json"
ARCHIVO_COBERTURA = "cobertura_clinicas.json"
ARCHIVO_PRESUPUESTO_SERP = "presupuesto_serpapi.json"
LIMITE_MENSUAL_SERPAPI = int(os.getenv("LIMITE_MENSUAL_SERPAPI", "250"))
RECONTACTO_DIAS = int(os.getenv("RECONTACTO_DIAS", "21"))
MAX_RECICLADOS_POR_CICLO = int(os.getenv("MAX_RECICLADOS_POR_CICLO", "8"))
MAX_FALLOS_SEGUIDOS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def _escribir_alerta(motivo, detalle=""):
    estado = {
        "linea": "clinicas",
        "motivo": motivo,
        "detalle": detalle,
        "timestamp": obtener_ahora_chile().strftime("%d/%m/%Y %H:%M"),
    }
    try:
        with open(ARCHIVO_ALERTA, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("No se pudo escribir el archivo de alerta.")


def _limpiar_alerta():
    if os.path.exists(ARCHIVO_ALERTA):
        try:
            os.remove(ARCHIVO_ALERTA)
        except Exception:
            logging.exception("No se pudo limpiar el archivo de alerta.")


# --- RESUMEN POR EMAIL ---
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO") or "rvillegasburgos@gmail.com"


def enviar_resumen_email(asunto, cuerpo):
    """
    Envía el resumen del ciclo por Gmail SMTP (requiere una 'contraseña de
    aplicación' de Google, no la contraseña normal de la cuenta). Si no está
    configurado (GMAIL_USER/GMAIL_APP_PASSWORD), se omite silenciosamente:
    el email es un extra, nunca debe hacer fallar el ciclo de prospección.
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logging.warning("GMAIL_USER/GMAIL_APP_PASSWORD no configurados, se omite el resumen por email.")
        return False
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_DESTINO
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [EMAIL_DESTINO], msg.as_string())
        logging.info("Resumen del ciclo enviado por email a %s.", EMAIL_DESTINO)
        return True
    except Exception:
        logging.exception("No se pudo enviar el resumen del ciclo por email.")
        return False


def _nuevo_resumen():
    return {"nuevos_leads": [], "mensajes": [], "reciclados": 0, "alertas": []}


def _enviar_resumen_si_corresponde(resumen, ahora):
    """
    Solo envía el email si pasó algo relevante en el ciclo (leads nuevos,
    mensajes enviados, alertas, o reciclaje) — si no hubo nada que reportar
    (ej. fuera de horario o ciclo vacío), no manda correo.
    """
    if not (resumen["nuevos_leads"] or resumen["mensajes"] or resumen["reciclados"] or resumen["alertas"]):
        print("📪 Nada relevante que reportar este ciclo, no se envía resumen por email.")
        return

    asunto = f"GestiónVital (Clínicas) - Resumen {ahora.strftime('%d/%m %H:%M')}"
    lineas = []

    if resumen["alertas"]:
        lineas.append("ALERTAS:")
        lineas += [f"- {a}" for a in resumen["alertas"]]
        lineas.append("")

    if resumen["nuevos_leads"]:
        lineas.append(f"Leads nuevos encontrados ({len(resumen['nuevos_leads'])}):")
        lineas += [f"- {l['Evento']} ({l['Ubicacion']})" for l in resumen["nuevos_leads"]]
        lineas.append("")

    if resumen["mensajes"]:
        exitosos = [m for m in resumen["mensajes"] if m["ok"]]
        fallidos = [m for m in resumen["mensajes"] if not m["ok"]]
        lineas.append(f"Mensajes de secuencia enviados ({len(exitosos)} ok, {len(fallidos)} fallidos):")
        for m in resumen["mensajes"]:
            lineas.append(f"- [{'OK' if m['ok'] else 'FALLO'}] {m['Evento']} - Día {m['dia']}")
        lineas.append("")

    if resumen["reciclados"]:
        lineas.append(f"Leads reciclados para recontacto: {resumen['reciclados']}")

    enviar_resumen_email(asunto, "\n".join(lineas))

# --- UTILIDADES DE HUMANIZACIÓN ---
def aplicar_spintax(texto):
    """ Selecciona una opción aleatoria entre {opcion1|opcion2} para variar el mensaje """
    def reemplazar(match):
        opciones = match.group(1).split('|')
        return random.choice(opciones)
    return re.sub(r'\{([^{}]*)\}', reemplazar, texto)

def obtener_ahora_chile():
    """
    Devuelve la hora actual de Chile como datetime naive (sin tzinfo), para que
    siga siendo comparable con las fechas naive que se parsean desde el CSV
    (datetime.strptime(...) en Fecha_Contacto).

    Chile continental SÍ cambia de hora (UTC-3 en horario de verano, UTC-4 en
    invierno), así que un offset fijo queda desfasado ~1 hora media temporada
    del año. Usamos zoneinfo con la base de datos IANA real; si no está
    disponible (ej. Windows sin el paquete tzdata instalado), caemos a UTC-3
    fijo como aproximación.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
    except Exception:
        logging.warning(
            "No se pudo usar zoneinfo/tzdata para America/Santiago, usando "
            "fallback UTC-3 fijo (puede estar desfasado ~1h en horario de invierno)."
        )
        return datetime.utcnow() - timedelta(hours=3)

def limpiar_acentos(text):
    if not isinstance(text, str): return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')


def reciclar_leads_antiguos(df, ahora):
    if df.empty:
        return df, 0

    candidatos = []
    for idx, row in df.iterrows():
        estado = str(row.get("Estado", ""))
        fecha_contacto = str(row.get("Fecha_Contacto", "")).strip()

        if estado not in ["Finalizado", "Error", "Rechazado"]:
            continue
        if not fecha_contacto:
            continue

        try:
            ultima_fecha = datetime.strptime(fecha_contacto, "%d/%m/%Y %H:%M")
            if (ahora - ultima_fecha).days >= RECONTACTO_DIAS:
                candidatos.append(idx)
        except Exception:
            continue

    if not candidatos:
        return df, 0

    random.shuffle(candidatos)
    reciclados = candidatos[:MAX_RECICLADOS_POR_CICLO]
    for idx in reciclados:
        df.at[idx, "Estado"] = "Nuevo"
        df.at[idx, "Dia_Secuencia"] = 0
        df.at[idx, "Fecha_Contacto"] = ""

    return df, len(reciclados)

# --- EXTRACTOR DE CORREOS ---
PAGINAS_CONTACTO = ["/contacto", "/contact", "/contactenos", "/nosotros", "/about", "/sobre-nosotros"]


def _extraer_emails_de_html(html):
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
    filtrados = [e for e in emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))]
    if not filtrados:
        return ""
    prioritarios = [e for e in filtrados if any(p in e.lower() for p in ['contacto', 'info', 'ventas'])]
    return (prioritarios[0] if prioritarios else filtrados[0]).lower()


def buscar_email_en_web(url):
    """
    Busca un email en la home del sitio; si no encuentra nada, intenta un par de
    páginas de contacto típicas antes de rendirse (mejora la tasa de captura de
    email sin disparar más búsquedas de SerpAPI, ya que son requests directos).
    """
    if not url or not url.startswith("http"):
        return ""
    headers = {'User-Agent': 'Mozilla/5.0'}
    base = url.rstrip('/')

    try:
        response = requests.get(url, headers=headers, timeout=12)
        email = _extraer_emails_de_html(response.text)
        if email:
            return email
    except Exception:
        pass

    for pagina in PAGINAS_CONTACTO:
        try:
            response = requests.get(base + pagina, headers=headers, timeout=8)
            if response.status_code != 200:
                continue
            email = _extraer_emails_de_html(response.text)
            if email:
                return email
        except Exception:
            continue

    return ""

# --- BÚSQUEDA AUTOMÁTICA ---
# Las 52 comunas de la Región Metropolitana, para cobertura exhaustiva.
COMUNAS_OBJETIVO = [
    # Provincia de Santiago (32)
    "Santiago Centro", "Cerrillos", "Cerro Navia", "Conchalí", "El Bosque",
    "Estación Central", "Huechuraba", "Independencia", "La Cisterna", "La Florida",
    "La Granja", "La Pintana", "La Reina", "Las Condes", "Lo Barnechea",
    "Lo Espejo", "Lo Prado", "Macul", "Maipú", "Ñuñoa",
    "Pedro Aguirre Cerda", "Peñalolén", "Providencia", "Pudahuel", "Quilicura",
    "Quinta Normal", "Recoleta", "Renca", "San Joaquín", "San Miguel",
    "San Ramón", "Vitacura",
    # Provincia Cordillera (3)
    "Puente Alto", "Pirque", "San José de Maipo",
    # Provincia Chacabuco (3)
    "Colina", "Lampa", "Til Til",
    # Provincia Maipo (4)
    "San Bernardo", "Buin", "Paine", "Calera de Tango",
    # Provincia Melipilla (5)
    "Melipilla", "Alhué", "Curacaví", "María Pinto", "San Pedro",
    # Provincia Talagante (5)
    "Talagante", "El Monte", "Isla de Maipo", "Padre Hurtado", "Peñaflor",
]

# Variantes del rubro para capturar negocios que no se autodescriben como
# "clínica estética" pero pertenecen al mismo mercado objetivo.
TERMINOS_BUSQUEDA = [
    "Clinica Estetica",
    "Centro de Estetica",
    "Medicina Estetica",
    "Spa Facial",
    "Depilacion Laser",
    "Botox y Rellenos",
]


def _cargar_cobertura():
    if os.path.exists(ARCHIVO_COBERTURA):
        try:
            with open(ARCHIVO_COBERTURA, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.exception("No se pudo leer %s, se reconstruye desde cero.", ARCHIVO_COBERTURA)
    return None


def _guardar_cobertura(estado):
    try:
        with open(ARCHIVO_COBERTURA, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("No se pudo escribir %s.", ARCHIVO_COBERTURA)


def _nueva_vuelta(vuelta_anterior=0):
    combos = [[comuna, termino] for comuna in COMUNAS_OBJETIVO for termino in TERMINOS_BUSQUEDA]
    random.shuffle(combos)
    return {"pendientes": combos, "vuelta": vuelta_anterior + 1}


def obtener_siguiente_combo():
    """
    Devuelve (zona, termino) siguiente a buscar, sin marcarla aún como cubierta
    (eso lo hace marcar_combo_cubierto una vez que la búsqueda efectivamente
    corrió). Lleva un registro persistente de qué combinaciones comuna+término
    ya se cubrieron en esta "vuelta" de barrido exhaustivo (52 comunas x N
    términos). Al agotar todas las combinaciones, comienza una vuelta nueva
    (re-mezclada) automáticamente.
    """
    estado = _cargar_cobertura()
    if not estado or not estado.get("pendientes"):
        estado = _nueva_vuelta(estado.get("vuelta", 0) if estado else 0)
        _guardar_cobertura(estado)
        print(f"🔄 Iniciando vuelta de barrido exhaustivo N°{estado['vuelta']} "
              f"({len(estado['pendientes'])} combinaciones comuna+término).")

    zona, termino = estado["pendientes"][0]
    print(f"📍 Combinación elegida (vuelta {estado['vuelta']}, quedan "
          f"{len(estado['pendientes'])} por cubrir): {termino} en {zona}")
    return zona, termino


def marcar_combo_cubierto(zona, termino):
    """
    Confirma que la combinación se buscó de verdad y la saca de pendientes.
    Se llama solo cuando SerpAPI respondió (ok o sin_resultados) — si hubo
    cuota agotada o error de red, la combinación se deja pendiente para
    reintentarla en el próximo ciclo, en vez de darla por cubierta sin haberla
    buscado realmente.
    """
    estado = _cargar_cobertura()
    if not estado or not estado.get("pendientes"):
        return
    if estado["pendientes"][0] == [zona, termino]:
        estado["pendientes"].pop(0)
        _guardar_cobertura(estado)


def _cargar_presupuesto_serp():
    if os.path.exists(ARCHIVO_PRESUPUESTO_SERP):
        try:
            with open(ARCHIVO_PRESUPUESTO_SERP, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.exception("No se pudo leer %s, se reinicia el contador.", ARCHIVO_PRESUPUESTO_SERP)
    return {"mes": None, "usadas": 0}


def _guardar_presupuesto_serp(estado):
    try:
        with open(ARCHIVO_PRESUPUESTO_SERP, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("No se pudo escribir %s.", ARCHIVO_PRESUPUESTO_SERP)


def _reservar_busqueda_serp(ahora):
    """
    Reparte el cupo mensual de SerpAPI (LIMITE_MENSUAL_SERPAPI, por defecto 250)
    de forma pareja a lo largo del mes, en vez de dejar que un cron por hora
    queme todo el cupo en los primeros días. Si hay cupo disponible para el día
    de hoy, lo reserva (incrementa el contador) y devuelve True; si no, False.
    """
    mes_actual = ahora.strftime("%Y-%m")
    estado = _cargar_presupuesto_serp()
    if estado.get("mes") != mes_actual:
        estado = {"mes": mes_actual, "usadas": 0}

    dias_en_mes = calendar.monthrange(ahora.year, ahora.month)[1]
    permitido_hasta_hoy = min(
        LIMITE_MENSUAL_SERPAPI,
        math.ceil(ahora.day * LIMITE_MENSUAL_SERPAPI / dias_en_mes),
    )

    if estado["usadas"] >= permitido_hasta_hoy:
        _guardar_presupuesto_serp(estado)
        print(f"💸 Cupo de SerpAPI del día agotado ({estado['usadas']}/{permitido_hasta_hoy} "
              f"permitidas a esta altura del mes, límite mensual {LIMITE_MENSUAL_SERPAPI}). "
              f"Se omite la búsqueda de este ciclo.")
        return False

    estado["usadas"] += 1
    _guardar_presupuesto_serp(estado)
    print(f"💳 Presupuesto SerpAPI: {estado['usadas']}/{LIMITE_MENSUAL_SERPAPI} usadas este mes "
          f"({permitido_hasta_hoy} permitidas a esta altura del mes).")
    return True


def buscar_y_agregar_nuevos(df_actual):
    """
    Busca nuevos leads en SerpAPI para la siguiente combinación comuna+término
    pendiente del barrido exhaustivo (ver obtener_siguiente_combo), respetando
    el cupo mensual configurado en LIMITE_MENSUAL_SERPAPI.
    Devuelve (df_actualizado, resultado) donde resultado es uno de:
    "ok", "sin_resultados", "cuota_agotada", "error_api", "presupuesto_agotado".
    """
    ahora_cl = obtener_ahora_chile()
    if not _reservar_busqueda_serp(ahora_cl):
        return df_actual, "presupuesto_agotado"

    zona_objetivo, termino_objetivo = obtener_siguiente_combo()
    print(f"🔍 Buscando nuevos leads: '{termino_objetivo}' en {zona_objetivo}...")
    params = {
        "engine": "google_maps",
        "q": f"{termino_objetivo} {zona_objetivo} Chile",
        "api_key": SERP_KEY,
        "num": 15,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        print(f"🔎 SerpAPI status: {response.status_code}")
        data = response.json()

        if "error" in data:
            # SerpAPI devuelve HTTP 200 con {"error": "..."} en casos de cuota
            # agotada / api_key inválida, distinto de "sin resultados".
            print(f"🚫 SerpAPI error: {data['error']}")
            logging.error("SerpAPI error (clínicas): %s", data["error"])
            return df_actual, "cuota_agotada"

        results = data.get("local_results", [])
        print(f"🔎 SerpAPI local_results: {len(results)}")
        nuevos_leads = []
        tels_en_base = set()
        if not df_actual.empty and "Telefono" in df_actual.columns:
            tels_en_base = set(
                df_actual["Telefono"]
                .astype(str)
                .str.replace(".0", "", regex=False)
                .str[-9:]
                .tolist()
            )
        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0
        for place in results:
            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not place.get("website") or not raw_tel or len(raw_tel) < 8: continue
            if raw_tel[-9:] not in tels_en_base:
                ultimo_id += 1
                nuevos_leads.append({
                    "Id": int(ultimo_id), "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                    "Hora": ahora_cl.strftime("%H:%M"), "Evento": place.get("title", "Clinica"),
                    "Ministerio": f"Prospeccion Automatica - {termino_objetivo}", "Ubicacion": zona_objetivo, "Estado": "Nuevo",
                    "Telefono": raw_tel, "Email": buscar_email_en_web(place.get("website")),
                    "Email_Enviado": "No", "Dia_Secuencia": 0, "Fecha_Contacto": ""
                })
                tels_en_base.add(raw_tel[-9:])
        marcar_combo_cubierto(zona_objetivo, termino_objetivo)
        if nuevos_leads:
            print(f"➕ Leads agregados: {len(nuevos_leads)}")
            return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True), "ok"
        print("📭 SerpAPI no devolvió leads nuevos (duplicados o sin teléfono/web válido).")
        return df_actual, "sin_resultados"
    except Exception as e:
        print(f"❌ Error búsqueda: {e}")
        logging.exception("Error al buscar nuevos leads (clínicas).")
        return df_actual, "error_api"

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    return _evo_enviar_mensaje_texto(EVO_URL, EVO_TOKEN, EVO_INSTANCE, numero, mensaje)

def obtener_mensaje_secuencia(nombre, ubicacion, dia):
    """
    Secuencia de 4 mensajes: corta y natural (mismo criterio que los mensajes
    manuales del dashboard), cerrando con una pregunta sobre un dolor concreto
    en vez de una oferta directa. Usa *texto* (un solo asterisco) para negrita
    de WhatsApp — ** (doble asterisco) no es sintaxis válida de WhatsApp y se
    muestra literal con los asteriscos visibles.
    """
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "su zona"

    # Textos con Spintax para evitar detección de patrones repetitivos
    if dia == 1:
        msg = ("{Hola,|Buen día,|Hola, ¿qué tal?} soy Rodrigo, de GestiónVital. Vi *{nombre}* en {zona} y "
               "{trabajo ayudando a|apoyo a} negocios como el suyo a organizar la agenda, las fichas y el "
               "control de insumos. {¿Cómo están llevando|¿Qué están usando para} la agenda hoy en día?")
    elif dia == 2:
        msg = ("{Hola de nuevo|Hola nuevamente}, disculpen si insisto. Sé que en *{nombre}* debe haber "
               "bastante movimiento — {justo por eso|por lo mismo} creo que les puede servir ordenar la "
               "agenda y el seguimiento de clientes en un solo lugar. {¿Les interesaría saber más|Vale la "
               "pena que les cuente cómo funciona}?")
    elif dia == 3:
        msg = ("{Hola|Buen día}, entiendo que estos mensajes se pierden fácil entre el día a día. Solo "
               "quería preguntarles: {¿cómo están manejando hoy|¿qué usan hoy para} las citas y el "
               "seguimiento de clientes en *{nombre}*? Tengo algunas ideas que podrían servirles.")
    elif dia == 4:
        msg = ("Último mensaje de mi parte, no quiero ser inoportuno. Si en algún momento *{nombre}* "
               "necesita apoyo para organizar la agenda o los procesos del día a día, aquí quedo. "
               "{¡Mucho éxito!|Éxito con todo!}")
    else: return ""

    return aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()
    resumen = _nuevo_resumen()

    # Restricción Lunes-Sábado 10:00 a 18:30 (Horario más conservador)
    if ahora.weekday() > 5 or not (10 <= ahora.hour <= 18):
        print(f"🕒 Fuera de horario de envío (Hora Chile: {ahora.strftime('%H:%M')}).")
        return

    estado_conexion = verificar_estado_conexion(EVO_URL, EVO_INSTANCE, EVO_TOKEN)
    if estado_conexion != "open":
        print(f"🔴 Sesión de WhatsApp no está 'open' (estado: {estado_conexion}). Abortando ciclo sin tocar leads.")
        logging.error("Sesión de WhatsApp caída o desconocida (estado=%s). Deteniendo ciclo.", estado_conexion)
        _escribir_alerta("sesion_whatsapp_caida", f"Estado reportado: {estado_conexion}")
        resumen["alertas"].append(f"Sesión de WhatsApp caída o desconocida (estado={estado_conexion}).")
        _enviar_resumen_si_corresponde(resumen, ahora)
        sys.exit(1)

    if not os.path.exists(ARCHIVO_LEADS): return

    df = pd.read_csv(ARCHIVO_LEADS)
    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")
    
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get('Fecha_Contacto', '')): continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada", "Error"]: continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        if row["Estado"] == "Contactado":
            try:
                ultima_fecha = datetime.strptime(str(row['Fecha_Contacto']), "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 90000: continue
            except: pass

        if row["Estado"] == "Contactado" and dia_act < 4:
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({'idx': idx, 'dia': 1})

    # MEZCLAR Y LIMITAR (Máximo 5 envíos por ciclo para seguridad)
    random.shuffle(candidatos)
    candidatos = candidatos[:5]

    if not candidatos:
        print("📭 Nada pendiente. Buscando nuevos leads...")
        total_antes = len(df)
        df, resultado_busqueda = buscar_y_agregar_nuevos(df)
        if len(df) > total_antes:
            nuevos = df.tail(len(df) - total_antes)
            resumen["nuevos_leads"] = [
                {"Evento": r["Evento"], "Ubicacion": r["Ubicacion"]} for _, r in nuevos.iterrows()
            ]
        df.to_csv(ARCHIVO_LEADS, index=False)
        if resultado_busqueda == "cuota_agotada":
            _escribir_alerta("serpapi_cuota_agotada", "SerpAPI devolvió un error (posible cuota agotada o api_key inválida).")
            resumen["alertas"].append("SerpAPI dejó de responder (posible cuota agotada o api_key inválida).")
            enviar_alerta_whatsapp(
                EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR,
                "⚠️ GestiónVital: SerpAPI dejó de responder (posible cuota agotada). Revisar api_key de clínicas.",
            )

        # Recalcular candidatos luego de agregar leads nuevos para enviar en el mismo run
        candidatos = []
        for idx, row in df.iterrows():
            if hoy_str in str(row.get("Fecha_Contacto", "")):
                continue
            if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada", "Error"]:
                continue

            dia_act = int(row.get("Dia_Secuencia", 0))
            if row["Estado"] == "Contactado":
                try:
                    ultima_fecha = datetime.strptime(str(row["Fecha_Contacto"]), "%d/%m/%Y %H:%M")
                    if (ahora - ultima_fecha).total_seconds() < 90000:
                        continue
                except Exception:
                    pass

            if row["Estado"] == "Contactado" and dia_act < 4:
                candidatos.append({"idx": idx, "dia": dia_act + 1})
            elif row["Estado"] == "Nuevo":
                candidatos.append({"idx": idx, "dia": 1})

        random.shuffle(candidatos)
        candidatos = candidatos[:5]

        if not candidatos:
            print("📭 Aun así no hay candidatos para enviar después de buscar nuevos leads.")
            print("♻️ Intentando reciclar leads antiguos...")
            df, total_reciclados = reciclar_leads_antiguos(df, ahora)
            resumen["reciclados"] = total_reciclados
            if total_reciclados > 0:
                print(f"♻️ Leads reciclados para recontacto: {total_reciclados}")
                df.to_csv(ARCHIVO_LEADS, index=False)
            else:
                print("📭 Sin leads reciclables. Conviene ampliar comunas/canales de captación.")
            _enviar_resumen_si_corresponde(resumen, ahora)
            return

    print(f"🚀 Procesando ráfaga de {len(candidatos)} envíos (Hora Chile: {ahora.strftime('%H:%M')})...")

    fallos_seguidos = 0
    for i, item in enumerate(candidatos):
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]

        tel_final = _normalizar_telefono_chile(row["Telefono"])

        msg = obtener_mensaje_secuencia(row["Evento"], row["Ubicacion"], dia_obj)
        if not msg: continue

        print(f"[{i+1}/{len(candidatos)}] Enviando a: {row['Evento']}...")

        if enviar_mensaje_texto(tel_final, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 4 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ✅ Día {dia_obj} enviado.")
            resumen["mensajes"].append({"Evento": row["Evento"], "dia": dia_obj, "ok": True})
            fallos_seguidos = 0
            _limpiar_alerta()
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ❌ Fallo técnico.")
            resumen["mensajes"].append({"Evento": row["Evento"], "dia": dia_obj, "ok": False})
            fallos_seguidos += 1

        df.to_csv(ARCHIVO_LEADS, index=False)

        if fallos_seguidos >= MAX_FALLOS_SEGUIDOS:
            motivo = f"{fallos_seguidos} envíos seguidos fallaron con sesión reportada como 'open' (posible degradación/soft-ban)."
            print(f"🔴 {motivo} Abortando el resto del ciclo.")
            logging.error(motivo)
            _escribir_alerta("fallos_envio_seguidos", motivo)
            resumen["alertas"].append(motivo)
            enviar_alerta_whatsapp(
                EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR,
                f"⚠️ GestiónVital (clínicas): {motivo}",
            )
            break

        # Pausa larga entre mensajes (4 a 8 minutos)
        if i < len(candidatos) - 1:
            espera = random.randint(240, 480)
            print(f"   ⏳ Pausa de seguridad: {espera} seg...")
            time.sleep(espera)

    print("🏁 Ciclo completado.")
    _enviar_resumen_si_corresponde(resumen, ahora)

if __name__ == "__main__":
    ejecutar_ciclo()