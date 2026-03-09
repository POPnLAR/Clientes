import pandas as pd
import requests
import os
import random
import time
import unicodedata
import re
from datetime import datetime, timedelta
import logging

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
# Usamos un CSV diferente para no mezclar las bases de datos
ARCHIVO_ALMACENES = "prospeccion_almacenes_pro.csv"
# Lista de comunas objetivo (puedes editarla sin tocar el código)
COMUNAS_OBJETIVO = ["Providencia", "Las Condes", "La Florida", "San Miguel", "El Bosque", "San Bernardo"]
# Límite diario de mensajes enviados para evitar baneos
MAX_MENSAJES_DIARIOS = int(os.getenv("MAX_MENSAJES_DIARIOS", "30"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# --- UTILIDADES ---
def obtener_ahora_chile():
    """
    Devuelve la hora actual de Chile utilizando zona horaria real si está disponible.
    Si no se puede usar zoneinfo (por versión de Python), cae a UTC-3.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return datetime.now(ZoneInfo("America/Santiago"))
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


def normalizar_telefono_chile(raw):
    """
    Normaliza distintos formatos de teléfono chileno a un formato consistente.
    Preferimos devolver '56XXXXXXXXX' cuando es posible.
    """
    digits = "".join(filter(str.isdigit, str(raw)))
    if not digits:
        return ""

    # Ya viene con código de país
    if digits.startswith("56") and len(digits) >= 11:
        return digits

    # Quitar ceros iniciales típicos (09..., 02..., etc.)
    while digits.startswith("0"):
        digits = digits[1:]

    # Celular típico 9XXXXXXXX
    if len(digits) == 9 and digits.startswith("9"):
        return "56" + digits

    # Fijo típico 2XXXXXXX u otros códigos de área de 1 dígito + 7
    if len(digits) == 9 and not digits.startswith("9"):
        return "56" + digits

    # Si hay más de 9 dígitos, intenta con los últimos 9
    if len(digits) > 9:
        ultimos = digits[-9:]
        if len(ultimos) == 9:
            return "56" + ultimos

    return digits

# --- BÚSQUEDA DE ALMACENES EN GOOGLE MAPS ---
def buscar_y_agregar_almacenes(df_actual):
    if not SERP_KEY:
        print("❌ SERP_KEY no configurado, no se buscarán nuevos almacenes.")
        logging.error("SERP_KEY no configurado; omitiendo búsqueda de almacenes.")
        return df_actual

    comunas = COMUNAS_OBJETIVO
    zona = random.choice(comunas)
    ahora_cl = obtener_ahora_chile()

    print(f"🏪 Buscando Minimarkets y Almacenes en: {zona}...")
    params = {
        "engine": "google_maps",
        "q": f"Minimarket o Almacen en {zona} Chile",
        "api_key": SERP_KEY,
        "num": 20,
    }

    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        nuevos_leads = []

        tels_en_base = set()
        if not df_actual.empty and "Telefono" in df_actual.columns:
            for t in df_actual["Telefono"]:
                digits = "".join(filter(str.isdigit, str(t)))
                if len(digits) >= 8:
                    tels_en_base.add(digits[-9:])

        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty and "Id" in df_actual.columns else 0

        for place in results:
            raw_tel = place.get("phone", "")
            tel_norm = normalizar_telefono_chile(raw_tel)
            if not tel_norm or len(tel_norm) < 8:
                continue

            clave_tel = "".join(filter(str.isdigit, tel_norm))[-9:]
            if clave_tel not in tels_en_base:
                ultimo_id += 1
                nuevos_leads.append({
                    "Id": int(ultimo_id),
                    "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                    "Hora": ahora_cl.strftime("%H:%M"),
                    "Evento": place.get("title", "Almacen"),
                    "Ministerio": "App Almacen",
                    "Ubicacion": zona,
                    "Estado": "Nuevo",
                    "Telefono": tel_norm,
                    "Dia_Secuencia": 0,
                    "Fecha_Contacto": "",
                    "Resultado": "",
                    "Notas": "",
                    "Version_Mensaje": "",
                })
                tels_en_base.add(clave_tel)

        if nuevos_leads:
            return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    except Exception:
        logging.exception("❌ Error en búsqueda de almacenes")
        print("❌ Error en búsqueda de almacenes (ver logs).")
    return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    if not EVO_URL or not EVO_TOKEN:
        logging.error("EVO_URL o EVO_TOKEN no configurados; no se puede enviar mensaje.")
        return False
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    try:
        requests.post(
            f"{base_url}/chat/sendPresence/{EVO_INSTANCE}",
            json={"number": numero, "presence": "composing"},
            headers=headers,
            timeout=10,
        )
        time.sleep(random.randint(15, 30))  # Simular escritura

        payload = {
            "number": numero, 
            "options": {"delay": 2000, "presence": "composing"}, 
            "textMessage": {"text": mensaje}
        }
        res = requests.post(
            f"{base_url}/message/sendText/{EVO_INSTANCE}",
            json=payload,
            headers=headers,
            timeout=20,
        )
        if res.status_code not in [200, 201]:
            logging.error(
                "Error al enviar mensaje. Código HTTP: %s, respuesta: %s",
                res.status_code,
                res.text,
            )
        return res.status_code in [200, 201]
    except Exception:
        logging.exception("Excepción al enviar mensaje de texto.")
        return False

def obtener_mensaje_almacen(nombre, ubicacion, dia):
    """
    Genera el mensaje para el almacén y una etiqueta de versión para A/B testing.
    Devuelve (mensaje_texto, version).
    """
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "el sector"

    if dia == 1:
        # A/B testing sencillo: dos variantes del mensaje inicial
        variante = random.choice(["A", "B"])
        if variante == "A":
            # Versión original con link directo
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Mi nombre es Rodrigo. "
                "Paso seguido por {zona} y veo que en **{nombre}** "
                "{tienen mucha variedad|siempre tienen movimiento}.\n\n"
                "Les escribo porque desarrollamos una **app chilena** para dueños de almacenes "
                "que quieren {controlar su stock|ver sus ventas diarias|ordenar las cuentas} "
                "desde el celular de forma fácil. ✨\n\n"
                "{¿Les gustaría|¿Les interesa} que les envíe un videito de 1 minuto para que vean "
                "cómo les puede ayudar a ganar tiempo? ¡Saludos!\n\n"
                "https://gestionalmacenpro.cl"
            )
        else:
            # Versión sin link directo, CTA simple a responder "SI"
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Mi nombre es Rodrigo. "
                "Veo que en **{nombre}** en {zona} siempre hay movimiento.\n\n"
                "Estoy trabajando con una **app para almacenes** que ayuda a "
                "{controlar el stock|ver las ventas del día} "
                "desde el celular sin complicarse. ✨\n\n"
                "Si te interesa que te muestre cómo funciona en 1 minuto, "
                "respóndeme solo con un *SI*. 👍"
            )
        msg_final = aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))
        return msg_final, variante

    elif dia == 2:
        variante = "D2"
        msg = (
            "{Hola de nuevo|Hola nuevamente} 👋 Solo para complementar lo que les comenté antes: "
            "varios almacenes que usan la app nos dicen que lo que más valoran es "
            "{ver rápido cuánto vendieron en el día|tener claro qué productos se están moviendo más} "
            "y {evitar quedarse sin stock en cosas clave|saber a tiempo qué pedir a los proveedores}. 📊📱\n\n"
            "Si quieren, podemos agendar una mini demo de 10 minutos por WhatsApp para mostrarles "
            "cómo podría funcionar en **{nombre}** en {zona}. ¿Les tinca?"
        )
        msg_final = aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))
        return msg_final, variante

    return "", ""

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()
    
    # Horario Almacenero: 10 AM a 19 PM (Lunes a Sábado)
    if ahora.weekday() > 5 or not (10 <= ahora.hour <= 19): 
        print(f"🕒 Fuera de horario para almacenes.")
        return 

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

    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get('Fecha_Contacto', '')):
            continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Error", "Cita Agendada"]:
            continue
        # No seguir contactando leads ya clasificados como interesados / no interesados
        if row.get("Resultado") in ["Interesado", "No interesado", "Numero equivocado"]:
            continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        if row["Estado"] == "Contactado":
            try:
                ultima_fecha = datetime.strptime(str(row['Fecha_Contacto']), "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 90000: continue
            except Exception:
                logging.warning(
                    "No se pudo parsear Fecha_Contacto para Id %s: %s",
                    row.get("Id"),
                    row.get("Fecha_Contacto"),
                )

        if row["Estado"] == "Contactado" and dia_act < 2: # Secuencia más corta (2 días)
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({'idx': idx, 'dia': 1})

    # Límite muy conservador para evitar baneo
    random.shuffle(candidatos)
    candidatos = candidatos[:3] # Solo 3 almacenes por ciclo

    if not candidatos:
        print("📭 Buscando nuevos almacenes...")
        df = buscar_y_agregar_almacenes(df)
        df.to_csv(ARCHIVO_ALMACENES, index=False)
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