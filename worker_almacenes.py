import pandas as pd
import requests
import os
import random
import sys
import time
import unicodedata
import re
from datetime import datetime, timedelta
import logging

import agent_client
import captacion
import cobertura
import serp_client
from evo_client import (
    normalizar_telefono_chile,
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
# Usamos un CSV diferente para no mezclar las bases de datos
ARCHIVO_ALMACENES = "prospeccion_almacenes_pro.csv"
# Barrido sistemático de las 52 comunas de la Región Metropolitana x rubros (ver cobertura.py)
COMUNAS_OBJETIVO = cobertura.COMUNAS_RM
TERMINOS_BUSQUEDA = ["Minimarket", "Almacen", "Botilleria", "Emporio"]
ARCHIVO_COBERTURA = "cobertura_almacenes.json"
# Cupo mensual de SerpAPI de esta línea (clínicas usa otros 150 de los 250 totales)
ARCHIVO_PRESUPUESTO_SERP = "presupuesto_serpapi_almacenes.json"
LIMITE_MENSUAL_SERPAPI = int(os.getenv("LIMITE_MENSUAL_SERPAPI_ALMACENES", "100"))
# Ventana de envío (hora de Chile, lunes a sábado): desde HORA_INICIO_ENVIO:00 hasta HORA_FIN_ENVIO:59.
HORA_INICIO_ENVIO = int(os.getenv("HORA_INICIO_ENVIO", "9"))
HORA_FIN_ENVIO = int(os.getenv("HORA_FIN_ENVIO", "19"))
# Límite diario de mensajes enviados para evitar baneos
MAX_MENSAJES_DIARIOS = int(os.getenv("MAX_MENSAJES_DIARIOS", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# --- UTILIDADES ---
def obtener_ahora_chile():
    """
    Hora actual de Chile como datetime naive (sin tzinfo), comparable con las fechas
    naive que se leen del CSV (Fecha_Contacto). Usa la zona real America/Santiago si
    está disponible y cae a UTC-3 si no.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() - timedelta(hours=3)

def aplicar_spintax(texto):
    def reemplazar(match):
        opciones = match.group(1).split('|')
        return random.choice(opciones)
    return re.sub(r'\{([^{}]*)\}', reemplazar, texto)

def limpiar_acentos(text):
    if not isinstance(text, str):
        return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')


# --- BÚSQUEDA DE ALMACENES EN GOOGLE MAPS ---
def buscar_y_agregar_almacenes(df_actual):
    """
    Busca almacenes para la siguiente combinación comuna+rubro del barrido, pidiendo varias
    páginas mientras rindan. Solo agrega celulares con WhatsApp verificado; si el teléfono de
    Google es un fijo, intenta rescatar un celular desde el sitio web del negocio.
    """
    if not SERP_KEY:
        print("❌ SERP_KEY no configurado, no se buscarán nuevos almacenes.")
        logging.error("SERP_KEY no configurado; omitiendo búsqueda de almacenes.")
        return df_actual

    ahora_cl = obtener_ahora_chile()
    zona, termino = cobertura.siguiente(ARCHIVO_COBERTURA, COMUNAS_OBJETIVO, TERMINOS_BUSQUEDA)
    print(f"🏪 Buscando '{termino}' en: {zona}...")

    tels_en_base = set()
    if not df_actual.empty and "Telefono" in df_actual.columns:
        for t in df_actual["Telefono"]:
            digits = "".join(filter(str.isdigit, str(t)))
            if len(digits) >= 8:
                tels_en_base.add(digits[-9:])
    ultimo_id = int(df_actual["Id"].max()) if not df_actual.empty and "Id" in df_actual.columns else 0
    nuevos_leads = []
    descartes = {"sin_movil": 0, "cadena": 0, "duplicado": 0, "sin_whatsapp": 0}

    def procesar_pagina(resultados):
        nonlocal ultimo_id
        candidatos = captacion.preparar_candidatos(resultados, tels_en_base, "almacenes", descartes)
        confirmados = captacion.confirmar_con_whatsapp(candidatos, EVO_URL, EVO_TOKEN, EVO_INSTANCE, descartes)
        for cand, tel in confirmados:
            ultimo_id += 1
            nuevos_leads.append({
                "Id": int(ultimo_id),
                "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                "Hora": ahora_cl.strftime("%H:%M"),
                "Evento": cand["place"].get("title", "Almacen"),
                "Ministerio": "App Almacen",
                "Ubicacion": zona,
                "Estado": "Nuevo",
                "Telefono": tel,
                "Dia_Secuencia": 0,
                "Fecha_Contacto": "",
                "Resultado": "",
                "Notas": "WhatsApp obtenido de su sitio web" if cand["origen"] == "web" else "",
                "Version_Mensaje": "",
            })
        return len(confirmados)

    try:
        estado, paginas = serp_client.buscar_paginas(
            f"{termino} {zona} Chile", SERP_KEY,
            ARCHIVO_PRESUPUESTO_SERP, LIMITE_MENSUAL_SERPAPI, ahora_cl, procesar_pagina,
        )
    except Exception:
        logging.exception("❌ Error en búsqueda de almacenes")
        print("❌ Error en búsqueda de almacenes (ver logs).")
        return df_actual

    if estado == "presupuesto_agotado":
        return df_actual
    print(f"🧹 Descartados: {descartes} ({paginas} página(s) consultada(s))")
    if estado in ("ok", "sin_resultados"):
        cobertura.marcar_cubierto(ARCHIVO_COBERTURA, zona, termino)
    if estado == "cuota_agotada":
        enviar_alerta_whatsapp(
            EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR,
            "⚠️ GestiónVital: SerpAPI dejó de responder en almacenes (posible cuota agotada). Revisar api_key.",
        )
    if nuevos_leads:
        return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    print("📭 La búsqueda no dejó leads nuevos (duplicados, sin celular o sin WhatsApp).")
    return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    return _evo_enviar_mensaje_texto(EVO_URL, EVO_TOKEN, EVO_INSTANCE, numero, mensaje)


def _rubro_en_plural(nombre):
    """
    Rubro del negocio, deducido de su nombre, para que el mensaje hable de "botillerías" con una
    botillería y no de "almacenes". Si el nombre no lo dice, se usa una fórmula neutra.
    """
    n = limpiar_acentos(nombre).lower()
    if re.search(r"botiller|licorer", n):
        return "botillerías"
    if re.search(r"mini ?market|minimercado", n):
        return "minimarkets"
    if "emporio" in n:
        return "emporios"
    if re.search(r"\balmacen", n):
        return "almacenes"
    return "negocios de barrio"


def obtener_mensaje_almacen(nombre, ubicacion, dia):
    """
    Genera el mensaje para el negocio y una etiqueta de versión para A/B testing.
    Devuelve (mensaje_texto, version). Habla del rubro real del negocio (botillería, minimarket...)
    y cierra con una pregunta simple, sin ofrecer enviar videos.
    """
    rubro = _rubro_en_plural(nombre)
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "el sector"

    def armar(msg):
        # Los marcadores propios se reemplazan ANTES del spintax: {plural} sin "|" sería
        # tomado como una opción única y quedaría escrito literalmente.
        return aplicar_spintax(
            msg.replace("{nombre}", nombre).replace("{zona}", zona).replace("{plural}", rubro)
        )

    if dia == 1:
        # A/B testing sencillo: dos variantes del mensaje inicial
        variante = random.choice(["A", "B"])
        if variante == "A":
            # Con link directo
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Mi nombre es Rodrigo. "
                "Paso seguido por {zona} y veo que en *{nombre}* "
                "{tienen mucha variedad|siempre tienen movimiento}.\n\n"
                "Les escribo porque desarrollamos una *app chilena* para dueños de {plural} "
                "que quieren {controlar su stock|ver sus ventas diarias|ordenar las cuentas} "
                "desde el celular de forma fácil. ✨\n\n"
                "{¿Les interesaría que les cuente cómo funciona?|¿Les gustaría saber cómo funciona?|"
                "¿Les hace sentido que conversemos un momento?} ¡Saludos!\n\n"
                "https://gestionalmacenpro.cl"
            )
        else:
            # Sin link directo, CTA simple a responder "SI"
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Mi nombre es Rodrigo. "
                "Veo que en *{nombre}* en {zona} siempre hay movimiento.\n\n"
                "Estoy trabajando con una *app para {plural}* que ayuda a "
                "{controlar el stock|ver las ventas del día} "
                "desde el celular sin complicarse. ✨\n\n"
                "Si te interesa saber cómo funciona, respóndeme solo con un *SI*. 👍"
            )
        return armar(msg), variante

    elif dia == 2:
        variante = "D2"
        msg = (
            "{Hola de nuevo|Hola nuevamente} 👋 Solo para complementar lo que les comenté antes: "
            "varios {plural} que usan la app nos dicen que lo que más valoran es "
            "{ver rápido cuánto vendieron en el día|tener claro qué productos se están moviendo más} "
            "y {evitar quedarse sin stock en cosas clave|saber a tiempo qué pedir a los proveedores}. 📊📱\n\n"
            "Si quieren, podemos agendar una mini demo de 10 minutos por WhatsApp para mostrarles "
            "cómo podría funcionar en *{nombre}* en {zona}. ¿Les tinca?"
        )
        return armar(msg), variante

    return "", ""


RESULTADOS_QUE_CIERRAN = {"interesado", "no interesado", "numero equivocado"}


def _armar_candidatos(df, ahora, respondieron, estado_resp):
    """
    Hasta 3 envíos [{'idx','dia'}] para este ciclo. Salta a quienes ya respondieron por
    WhatsApp (los atiende el agente) y a los ya clasificados. Si el agente no respondió
    (estado_resp == "error") solo se envían primeros mensajes, no seguimientos.
    """
    hoy_str = ahora.strftime("%d/%m/%Y")
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get("Fecha_Contacto", "")):
            continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Error", "Cita Agendada", "Agendado"]:
            continue
        if str(row.get("Resultado", "")).strip().lower() in RESULTADOS_QUE_CIERRAN:
            continue

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
                logging.warning(
                    "No se pudo parsear Fecha_Contacto para Id %s: %s", row.get("Id"), row.get("Fecha_Contacto")
                )

        if row["Estado"] == "Contactado" and dia_act < 2:  # secuencia más corta (2 días)
            candidatos.append({"idx": idx, "dia": dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({"idx": idx, "dia": 1})

    # Límite muy conservador para evitar baneo: solo 3 almacenes por ciclo
    random.shuffle(candidatos)
    return candidatos[:3]


# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()
    
    # Horario Almacenero: lunes a sábado, de HORA_INICIO_ENVIO:00 a HORA_FIN_ENVIO:59 (hora de Chile)
    if ahora.weekday() > 5 or not (HORA_INICIO_ENVIO <= ahora.hour <= HORA_FIN_ENVIO):
        print(f"🕒 Fuera de horario para almacenes (Hora Chile: {ahora.strftime('%A %H:%M')}; "
              f"se opera de lunes a sábado, {HORA_INICIO_ENVIO}:00 a {HORA_FIN_ENVIO}:59).")
        return

    estado_conexion = verificar_estado_conexion(EVO_URL, EVO_INSTANCE, EVO_TOKEN)
    if estado_conexion != "open":
        print(f"🔴 Sesión de WhatsApp no está 'open' (estado: {estado_conexion}). Abortando ciclo sin tocar leads.")
        logging.error("Sesión de WhatsApp caída o desconocida (estado=%s). Deteniendo ciclo.", estado_conexion)
        sys.exit(1)

    if not os.path.exists(ARCHIVO_ALMACENES):
        df = pd.DataFrame(columns=[
            "Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado",
            "Telefono","Dia_Secuencia","Fecha_Contacto","Resultado","Notas","Version_Mensaje"
        ])
    else:
        df = pd.read_csv(ARCHIVO_ALMACENES)

    # Aseguramos que estén todas las columnas requeridas
    columnas_minimas = [
        "Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado",
        "Telefono","Dia_Secuencia","Fecha_Contacto","Resultado","Notas","Version_Mensaje"
    ]
    for col in columnas_minimas:
        if col not in df.columns:
            if col == "Dia_Secuencia":
                df[col] = 0
            else:
                df[col] = ""

    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")

    # Control de límite diario de envíos
    if "Fecha_Contacto" in df.columns:
        enviados_hoy = df["Fecha_Contacto"].astype(str).str.startswith(hoy_str).sum()
    else:
        enviados_hoy = 0

    if enviados_hoy >= MAX_MENSAJES_DIARIOS:
        print(f"📵 Límite diario de mensajes alcanzado ({MAX_MENSAJES_DIARIOS}).")
        logging.warning("Límite diario de mensajes alcanzado: %s", MAX_MENSAJES_DIARIOS)
        return

    respondieron, estado_resp = agent_client.obtener_telefonos_que_respondieron()
    if respondieron:
        print(f"💬 {len(respondieron)} contactos ya respondieron: su secuencia automática queda pausada.")
    if estado_resp == "error":
        print("⚠️ No se pudo consultar al agente: este ciclo solo se envían primeros mensajes (no seguimientos).")

    candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp)

    if not candidatos:
        print("📭 Buscando nuevos almacenes...")
        antes = len(df)
        df = buscar_y_agregar_almacenes(df)
        df.to_csv(ARCHIVO_ALMACENES, index=False)
        despues = len(df)
        print(f"➕ Leads agregados: {max(0, despues-antes)}")

        # Si se agregaron leads, intentamos enviar en el mismo ciclo (para no esperar al próximo cron).
        candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp)

        if not candidatos:
            print("📭 Aún no hay candidatos después de buscar nuevos almacenes.")
            return

    print(f"🚀 Enviando a {len(candidatos)} almacenes...")
    
    for i, item in enumerate(candidatos):
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]
        tel_final = normalizar_telefono_chile(row.get("Telefono", ""))
        if not tel_final or len("".join(filter(str.isdigit, tel_final))) < 8:
            logging.error("Teléfono inválido para Id %s: %s", row.get("Id"), row.get("Telefono"))
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            continue

        msg, version = obtener_mensaje_almacen(row["Evento"], row["Ubicacion"], dia_obj)
        if not msg:
            continue

        if enviar_mensaje_texto(tel_final, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 2 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            if dia_obj == 1:
                df.at[idx, "Version_Mensaje"] = version
            print(f"   ✅ Día {dia_obj} enviado a {row['Evento']}.")
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")

        df.to_csv(ARCHIVO_ALMACENES, index=False)
        if i < len(candidatos) - 1:
            time.sleep(random.randint(300, 600)) # Pausas de 5-10 minutos

if __name__ == "__main__":
    ejecutar_ciclo()