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

import agent_client
import asignaciones
import captacion
import cobertura
import serp_client
import web_scraper
from evo_client import (
    normalizar_telefono_chile as _normalizar_telefono_chile,
    verificar_estado_conexion,
    enviar_mensaje_texto as _evo_enviar_mensaje_texto,
    enviar_alerta_whatsapp,
    es_movil_chileno,
    verificar_whatsapp,
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
LIMITE_MENSUAL_SERPAPI = int(os.getenv("LIMITE_MENSUAL_SERPAPI", "150"))  # de 250 totales; almacenes usa 100
RECONTACTO_DIAS = int(os.getenv("RECONTACTO_DIAS", "21"))
MAX_RECICLADOS_POR_CICLO = int(os.getenv("MAX_RECICLADOS_POR_CICLO", "8"))
MAX_FALLOS_SEGUIDOS = 3
# Ventana de envío (hora de Chile, lunes a sábado): desde HORA_INICIO_ENVIO:00 hasta HORA_FIN_ENVIO:59.
HORA_INICIO_ENVIO = int(os.getenv("HORA_INICIO_ENVIO", "9"))
HORA_FIN_ENVIO = int(os.getenv("HORA_FIN_ENVIO", "18"))

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


def reciclar_leads_antiguos(df, ahora, excluir=frozenset()):
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
        if agent_client.clave_telefono(row.get("Telefono", "")) in excluir:
            continue  # ya conversó con nosotros: no se le reinicia la secuencia automática

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
def buscar_email_en_web(url):
    """Busca un email en la home del sitio y, si no hay, en páginas de contacto típicas."""
    return web_scraper.obtener_contactos(url, necesita_email=True, necesita_movil=False)["email"]


# --- BÚSQUEDA AUTOMÁTICA ---
# Barrido sistemático de las 52 comunas de la Región Metropolitana (ver cobertura.py).
COMUNAS_OBJETIVO = cobertura.COMUNAS_RM

# Variantes del rubro para capturar negocios que no se autodescriben como
# "clínica estética" pero pertenecen al mismo mercado objetivo.
TERMINOS_BUSQUEDA = [
    "Clinica Estetica",
    "Centro de Estetica",
    "Medicina Estetica",
    "Spa Facial",
    "Depilacion Laser",
    "Botox y Rellenos",
    "Centro de Belleza",
    "Esteticista",
    "Cosmetologa",
    "Micropigmentacion",
    "Cejas y Pestanas",
    "Estetica Corporal",
    "Masajes Reductivos",
    "Tratamientos Faciales",
]
# Rubros que existían antes de que el estado de cobertura guardara su lista de rubros.
TERMINOS_PREVIOS = TERMINOS_BUSQUEDA[:6]


def obtener_siguiente_combo():
    return cobertura.siguiente(ARCHIVO_COBERTURA, COMUNAS_OBJETIVO, TERMINOS_BUSQUEDA, TERMINOS_PREVIOS)


def marcar_combo_cubierto(zona, termino):
    """Solo se llama cuando SerpAPI respondió (ok o sin resultados); si hubo cuota agotada o
    error de red la combinación queda pendiente para reintentarla en el próximo ciclo."""
    cobertura.marcar_cubierto(ARCHIVO_COBERTURA, zona, termino)


def buscar_y_agregar_nuevos(df_actual):
    """
    Busca nuevos leads para la siguiente combinación comuna+término del barrido, pidiendo
    varias páginas de resultados mientras rindan, dentro del cupo mensual de SerpAPI.
    Solo agrega celulares con WhatsApp verificado (si el teléfono de Google es un fijo, intenta
    rescatar un celular desde el sitio web del negocio).
    Devuelve (df_actualizado, resultado) con resultado en:
    "ok", "sin_resultados", "cuota_agotada", "error_api", "presupuesto_agotado".
    """
    ahora_cl = obtener_ahora_chile()
    zona_objetivo, termino_objetivo = obtener_siguiente_combo()
    print(f"🔍 Buscando nuevos leads: '{termino_objetivo}' en {zona_objetivo}...")

    tels_en_base = set()
    if not df_actual.empty and "Telefono" in df_actual.columns:
        tels_en_base = set(
            df_actual["Telefono"].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist()
        )
    ultimo_id = int(df_actual["Id"].max()) if not df_actual.empty else 0
    nuevos_leads = []
    descartes = {"sin_movil": 0, "cadena": 0, "duplicado": 0, "sin_whatsapp": 0}

    def procesar_pagina(resultados):
        nonlocal ultimo_id
        candidatos = captacion.preparar_candidatos(
            resultados, tels_en_base, "clinicas", descartes, necesita_email=True
        )
        confirmados = captacion.confirmar_con_whatsapp(candidatos, EVO_URL, EVO_TOKEN, EVO_INSTANCE, descartes)
        for cand, tel in confirmados:
            place = cand["place"]
            website = place.get("website") or ""
            if cand["origen"] == "web":
                email = cand["email"]  # ya se revisó el sitio al rescatar el celular
            else:
                email = buscar_email_en_web(website) if website else ""
            ultimo_id += 1
            nuevos_leads.append({
                "Id": int(ultimo_id), "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                "Hora": ahora_cl.strftime("%H:%M"), "Evento": place.get("title", "Clinica"),
                "Ministerio": f"Prospeccion Automatica - {termino_objetivo}", "Ubicacion": zona_objetivo,
                "Estado": "Nuevo", "Telefono": tel, "Email": email,
                "Email_Enviado": "No", "Dia_Secuencia": 0, "Fecha_Contacto": "",
                "Notas": "WhatsApp obtenido de su sitio web" if cand["origen"] == "web" else "",
            })
        return len(confirmados)

    estado, paginas = serp_client.buscar_paginas(
        f"{termino_objetivo} {zona_objetivo} Chile", SERP_KEY,
        ARCHIVO_PRESUPUESTO_SERP, LIMITE_MENSUAL_SERPAPI, ahora_cl, procesar_pagina,
    )
    if estado == "presupuesto_agotado":
        return df_actual, estado

    print(f"🧹 Descartados: {descartes} ({paginas} página(s) consultada(s))")
    if estado in ("ok", "sin_resultados"):
        marcar_combo_cubierto(zona_objetivo, termino_objetivo)
    if estado == "cuota_agotada":
        logging.error("SerpAPI error (clínicas): posible cuota agotada o api_key inválida.")
    if nuevos_leads:
        print(f"➕ Leads agregados: {len(nuevos_leads)}")
        return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True), "ok"
    if estado == "ok":
        estado = "sin_resultados"
    print("📭 La búsqueda no dejó leads nuevos (duplicados, sin celular o sin WhatsApp).")
    return df_actual, estado

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

def _armar_candidatos(df, ahora, respondieron, estado_resp, asignados=frozenset()):
    """
    Devuelve hasta 5 envíos [{'idx','dia'}] para este ciclo. Salta a quienes ya
    respondieron por WhatsApp (su conversación la lleva el agente, no la secuencia
    automática). Si el agente no respondió (estado_resp == "error") se frenan solo los
    seguimientos: un lead nuevo (día 1) no puede haber respondido aún.
    """
    hoy_str = ahora.strftime("%d/%m/%Y")
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get("Fecha_Contacto", "")):
            continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada", "Agendado", "Error"]:
            continue

        if asignaciones.clave_telefono(row.get("Telefono", "")) in asignados:
            continue  # lo atiende el vendedor: no se le escribe desde la secuencia automática

        if agent_client.clave_telefono(row.get("Telefono", "")) in respondieron:
            if not str(row.get("Notas", "")).strip() or str(row.get("Notas")) == "nan":
                df.at[idx, "Notas"] = "Respondió por WhatsApp: secuencia automática pausada"
            continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        if row["Estado"] == "Contactado":
            if estado_resp == "error":
                continue
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
    return candidatos[:5]


# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()
    resumen = _nuevo_resumen()

    # Restricción Lunes-Sábado, de HORA_INICIO_ENVIO:00 a HORA_FIN_ENVIO:59 (hora de Chile)
    if ahora.weekday() > 5 or not (HORA_INICIO_ENVIO <= ahora.hour <= HORA_FIN_ENVIO):
        print(f"🕒 Fuera de horario de envío (Hora Chile: {ahora.strftime('%A %H:%M')}; "
              f"se opera de lunes a sábado, {HORA_INICIO_ENVIO}:00 a {HORA_FIN_ENVIO}:59).")
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
    respondieron, estado_resp = agent_client.obtener_telefonos_que_respondieron()
    asignados = asignaciones.cargar_asignados()
    if respondieron:
        print(f"💬 {len(respondieron)} contactos ya respondieron: su secuencia automática queda pausada.")
    if estado_resp == "error":
        print("⚠️ No se pudo consultar al agente: este ciclo solo se envían primeros mensajes (no seguimientos).")

    candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp, asignados)

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
        candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp, asignados)

        if not candidatos:
            print("📭 Aun así no hay candidatos para enviar después de buscar nuevos leads.")
            print("♻️ Intentando reciclar leads antiguos...")
            df, total_reciclados = reciclar_leads_antiguos(df, ahora, respondieron | asignados)
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