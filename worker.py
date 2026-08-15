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

# --- UTILIDADES DE HUMANIZACIÓN ---
def aplicar_spintax(texto):
    """ Selecciona una opción aleatoria entre {opcion1|opcion2} para variar el mensaje """
    def reemplazar(match):
        opciones = match.group(1).split('|')
        return random.choice(opciones)
    return re.sub(r'\{([^{}]*)\}', reemplazar, texto)

def obtener_ahora_chile():
    """ GitHub corre en UTC. Chile es UTC-3. """
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
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "su zona"
    
    # Textos con Spintax para evitar detección de patrones repetitivos
    if dia == 1:
        msg = ("{Hola|Buen día|Hola, ¿qué tal?|Hola, ¿cómo están?} 👋 Mi nombre es Rodrigo de **GestiónVital**. "
               "Les escribo porque sigo de cerca los centros de estética en {zona} y me {gustó mucho|llamó la atención|encantó} la propuesta de *{nombre}*.\n\n"
               "{Trabajo ayudando a|Apoyo a} centros como el de ustedes a que el día a día sea más fluido. Me encantaría {compartirles|comentarles|mostrarles} algunas ideas sobre:\n\n"
               "✨ Cómo agilizar las respuestas para que ningún paciente se quede esperando.\n"
               "📋 Digitalizar las fichas para mayor tranquilidad de todos.\n"
               "📦 Optimizar el control de insumos de forma simple.\n\n"
               "{¿Tendrán 5 minutitos|¿Tendrán un espacio|¿Podríamos conversar brevemente} esta semana {para conocernos|de forma relajada}? Me encantaría conocerles.\n\n"
               "https://gestionvitalpro.cl")
    elif dia == 2:
        msg = ("{Hola de nuevo|Hola nuevamente}. 👋 Solo pasaba a saludar y dejarles un dato: en **GestiónVital** hemos visto que {pequeños ajustes|mejoras simples|cambios sencillos} en la organización pueden liberar mucho tiempo para los dueños de centros en {zona}.\n\n"
               "En *{nombre}* tienen un potencial tremendo. ¿Les parecería si coordinamos una breve llamada {para presentarnos|esta semana}?")
    elif dia == 3:
        msg = ("{¡Hola!|Buen día} 🏥 ¿Cómo va la semana en *{nombre}*?\n\n"
               "Les escribía porque estamos invitando a algunos centros {referentes|destacados|importantes} de {zona} a una charla sobre las nuevas tendencias de gestión para este 2026. Me gustaría mucho que ustedes formaran parte. {¿Les interesa|¿Les gustaría} que les cuente más?")
    elif dia == 4:
        msg = ("Estimados en *{nombre}*, imagino que deben estar con {muchas cositas|mucho trabajo|la agenda a tope}, así que no les quito más tiempo. 👋\n\n"
               "Solo quería agradecerles por el espacio. Les dejo mi contacto por aquí; si alguna vez sienten que necesitan un apoyo para organizar procesos o crecer, cuenten conmigo. ¡Mucho éxito!")
    else: return ""
    
    return aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()

    # Restricción Lunes-Sábado 10:00 a 18:30 (Horario más conservador)
    if ahora.weekday() > 5 or not (10 <= ahora.hour <= 18):
        print(f"🕒 Fuera de horario de envío (Hora Chile: {ahora.strftime('%H:%M')}).")
        return

    estado_conexion = verificar_estado_conexion(EVO_URL, EVO_INSTANCE, EVO_TOKEN)
    if estado_conexion != "open":
        print(f"🔴 Sesión de WhatsApp no está 'open' (estado: {estado_conexion}). Abortando ciclo sin tocar leads.")
        logging.error("Sesión de WhatsApp caída o desconocida (estado=%s). Deteniendo ciclo.", estado_conexion)
        _escribir_alerta("sesion_whatsapp_caida", f"Estado reportado: {estado_conexion}")
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
        df, resultado_busqueda = buscar_y_agregar_nuevos(df)
        df.to_csv(ARCHIVO_LEADS, index=False)
        if resultado_busqueda == "cuota_agotada":
            _escribir_alerta("serpapi_cuota_agotada", "SerpAPI devolvió un error (posible cuota agotada o api_key inválida).")
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
            if total_reciclados > 0:
                print(f"♻️ Leads reciclados para recontacto: {total_reciclados}")
                df.to_csv(ARCHIVO_LEADS, index=False)
            else:
                print("📭 Sin leads reciclables. Conviene ampliar comunas/canales de captación.")
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
            fallos_seguidos = 0
            _limpiar_alerta()
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ❌ Fallo técnico.")
            fallos_seguidos += 1

        df.to_csv(ARCHIVO_LEADS, index=False)

        if fallos_seguidos >= MAX_FALLOS_SEGUIDOS:
            motivo = f"{fallos_seguidos} envíos seguidos fallaron con sesión reportada como 'open' (posible degradación/soft-ban)."
            print(f"🔴 {motivo} Abortando el resto del ciclo.")
            logging.error(motivo)
            _escribir_alerta("fallos_envio_seguidos", motivo)
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

if __name__ == "__main__":
    ejecutar_ciclo()