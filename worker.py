import pandas as pd
import requests
import os
import random
import time
import unicodedata
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"

# --- UTILIDADES ---
def limpiar_acentos(text):
    if not isinstance(text, str): return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

# --- EXTRACTOR DE CORREOS (SCRAPING) ---
def buscar_email_en_web(url):
    if not url or not url.startswith("http"):
        return ""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code != 200: return ""
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', response.text)
        filtrados = [e for e in emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))]
        if filtrados:
            prioritarios = [e for e in filtrados if any(p in e.lower() for p in ['contacto', 'info', 'ventas'])]
            return prioritarios[0].lower() if prioritarios else filtrados[0].lower()
    except: pass
    return ""

# --- BÚSQUEDA AUTOMÁTICA ---
def buscar_y_agregar_nuevos(df_actual):
    comunas = ["Las Condes", "Providencia", "Vitacura", "Lo Barnechea", "Ñuñoa", "La Reina"]
    zona_objetivo = random.choice(comunas)
    print(f"🔍 Buscando nuevos leads en: {zona_objetivo}...")
    params = {"engine": "google_maps", "q": f"Clinica Estetica {zona_objetivo} Chile", "api_key": SERP_KEY, "num": 20}
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        nuevos_leads = []
        tels_en_base = set(df_actual['Telefono'].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist())
        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0
        for place in results:
            tiene_web = place.get("website")
            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not tiene_web or not raw_tel or len(raw_tel) < 8: continue
            if raw_tel[-9:] not in tels_en_base:
                email_hallado = buscar_email_en_web(tiene_web)
                ultimo_id += 1
                nuevos_leads.append({
                    "Id": int(ultimo_id), "Fecha": datetime.now().strftime("%d/%m/%Y"),
                    "Hora": datetime.now().strftime("%H:%M"), "Evento": place.get("title", "Clinica"),
                    "Ministerio": "Prospeccion Automatica", "Ubicacion": zona_objetivo, "Estado": "Nuevo",
                    "Telefono": raw_tel, "Email": email_hallado, "Email_Enviado": "No", "Dia_Secuencia": 0, "Fecha_Contacto": ""
                })
                tels_en_base.add(raw_tel[-9:])
        if nuevos_leads: return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    except Exception as e: print(f"❌ Error búsqueda: {e}")
    return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    try:
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", json={"number": numero, "presence": "composing"}, headers=headers)
        time.sleep(random.randint(5, 10))
        payload = {"number": numero, "options": {"delay": 1200}, "textMessage": {"text": mensaje}}
        res = requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", json=payload, headers=headers, timeout=20)
        return res.status_code in [200, 201]
    except: return False

def obtener_mensaje_secuencia(nombre, ubicacion, dia):
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "su zona"
    if dia == 1: return f"Estimados {nombre}. 👋 Soy Rodrigo de **GestiónVital**. Notamos brechas críticas en su clínica de {zona}. ¿Tienen 5 min para optimizar su rentabilidad?"
    if dia == 2: return f"Hola de nuevo {nombre}. 👋 Digitalizar su operación en {zona} puede subir su rentabilidad un 20%. ¿Conversamos?"
    if dia == 3: return f"Buen día {nombre}. 🏥 Los seleccionamos para el programa **Transformación Digital 2026** en {zona}. ¿Le interesa liderar el sector?"
    if dia == 4: return f"Estimados {nombre}, entiendo el ajetreo. 👋 Les dejo mi contacto por si deciden profesionalizar su clínica a futuro. ¡Éxito!"
    return ""

# --- CICLO PRINCIPAL REFORZADO ---
def ejecutar_ciclo():
    ahora = datetime.now()
    # Restricción Lunes-Sábado 9:00 a 19:00
    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print("Fuera de horario de envío.")
        return 

    if not os.path.exists(ARCHIVO_LEADS):
        # Si el archivo no existe, lo creamos vacío para empezar a buscar
        df = pd.DataFrame(columns=["Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado","Telefono","Email","Email_Enviado","Dia_Secuencia","Fecha_Contacto"])
    else:
        df = pd.read_csv(ARCHIVO_LEADS)
    
    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")
    candidatos = []

    # 1. BUSCAR MENSAJES DE SEGUIMIENTO (Día 2, 3, 4) Y NUEVOS EN COLA
    for idx, row in df.iterrows():
        if hoy_str in str(row.get('Fecha_Contacto', '')): continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada"]: continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        
        # Validación estricta de 23.5 horas para seguimientos
        if row["Estado"] == "Contactado":
            fecha_str = str(row.get('Fecha_Contacto', ''))
            try:
                ultima_fecha = datetime.strptime(fecha_str, "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 84600:
                    continue
            except:
                if fecha_str != "": continue

        if row["Estado"] == "Contactado" and dia_act < 4:
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({'idx': idx, 'dia': 1})

    # 2. ACCIÓN SI NO HAY NADA QUE HACER: BUSCAR SANGRE NUEVA INMEDIATAMENTE
    if not candidatos:
        print("📭 Nada pendiente por hoy. Iniciando búsqueda automática de nuevos clientes...")
        df = buscar_y_agregar_nuevos(df)
        # Recargar candidatos tras la búsqueda para procesar los nuevos de una vez
        for idx, row in df.iterrows():
            if row["Estado"] == "Nuevo" and len(candidatos) < 15:
                # Solo agregamos los que acabamos de meter (que no tengan fecha de contacto)
                if not str(row.get('Fecha_Contacto', '')):
                    candidatos.append({'idx': idx, 'dia': 1})

    # 3. EJECUCIÓN DE ENVÍOS (Máximo 20 por ciclo para evitar bloqueos)
    if not candidatos:
        print("😴 No se encontraron clientes nuevos ni seguimientos.")
        return

    print(f"🚀 Procesando {len(candidatos[:20])} envíos...")
    for item in candidatos[:20]:
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]
        
        tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        if len(tel) == 9: tel = "56" + tel
        
        msg = obtener_mensaje_secuencia(row["Evento"], row["Ubicacion"], dia_obj)
        
        if enviar_mensaje_texto(tel, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 4 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            # Guardamos tras cada mensaje para no perder progreso si falla el script
            df.to_csv(ARCHIVO_LEADS, index=False)
            print(f"✅ Día {dia_obj} enviado a {row['Evento']}")
            time.sleep(random.randint(150, 350)) # Espera aleatoria entre mensajes
        else:
            print(f"❌ Falló envío a {row['Evento']}")

if __name__ == "__main__":
    ejecutar_ciclo()

if __name__ == "__main__":
    ejecutar_ciclo()