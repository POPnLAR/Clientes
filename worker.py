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

# --- COMUNICACIONES (CON LOG DE ERROR DETALLADO) ---
def enviar_mensaje_texto(numero, mensaje):
    if not EVO_URL or not EVO_TOKEN:
        print("❌ Error: Faltan credenciales de Evolution en Variables de Entorno.")
        return False
    
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    
    try:
        # 1. Simular presencia
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", 
                     json={"number": numero, "presence": "composing"}, 
                     headers=headers, timeout=10)
        
        time.sleep(random.randint(5, 10))

        # 2. Enviar mensaje
        payload = {
            "number": numero, 
            "options": {"delay": 1200, "presence": "composing"}, 
            "textMessage": {"text": mensaje}
        }
        res = requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", 
                           json=payload, headers=headers, timeout=20)
        
        if res.status_code in [200, 201]:
            return True
        else:
            print(f"⚠️ Error API Evolution ({res.status_code}): {res.text}")
            return False
    except Exception as e: 
        print(f"❌ Error de red: {e}")
        return False

def obtener_mensaje_secuencia(nombre, ubicacion, dia):
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "su zona"
    if dia == 1:
        return (f"Hola, ¡buen día! 👋 Mi nombre es Rodrigo de **GestiónVital**. "
                f"Les escribo porque sigo de cerca los centros de estética en {zona} y me gustó mucho la propuesta de *{nombre}*.\n\n"
                f"Trabajo ayudando a centros como el de ustedes a que el día a día sea más fluido. Me encantaría compartirles algunas ideas sobre:\n\n"
                f"✨ Cómo agilizar las respuestas para que ningún paciente se quede esperando.\n"
                f"📋 Digitalizar las fichas para mayor tranquilidad de todos.\n"
                f"📦 Optimizar el control de insumos de forma simple.\n\n"
                f"¿Tendrán 5 minutitos esta semana para conversar de forma relajada? Me encantaría conocerles.")
    
    elif dia == 2:
        return (f"Hola de nuevo. 👋 Solo pasaba a saludar y dejarles un dato: en **GestiónVital** hemos visto que pequeños ajustes en la organización pueden liberar mucho tiempo para los dueños de centros en {zona}.\n\n"
                f"En *{nombre}* tienen un potencial tremendo. ¿Les parecería si coordinamos una breve llamada para presentarnos?")
    
    elif dia == 3:
        return (f"¡Hola! 🏥 ¿Cómo va la semana en *{nombre}*?\n\n"
                f"Les escribía porque estamos invitando a algunos centros referentes de {zona} a una charla sobre las nuevas tendencias de gestión para este 2026. Me gustaría mucho que ustedes formaran parte. ¿Les interesa que les cuente más?")
    
    elif dia == 4:
        return (f"Estimados en *{nombre}*, imagino que deben estar con muchas cositas, así que no les quito más tiempo. 👋\n\n"
                f"Solo quería agradecerles por el espacio. Les dejo mi contacto por aquí; si alguna vez sienten que necesitan un apoyo para organizar procesos o crecer, cuenten conmigo. ¡Que tengan mucho éxito!")
    
    return ""

# --- CICLO PRINCIPAL SEGURO (GUARDADO INSTANTÁNEO) ---
def ejecutar_ciclo():
    ahora = datetime.now()
    # Restricción: Lunes-Sábado 9:00 a 19:00
    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print("🕒 Fuera de horario de envío.")
        return 

    if not os.path.exists(ARCHIVO_LEADS):
        print("❌ El archivo de leads no existe.")
        return
        
    df = pd.read_csv(ARCHIVO_LEADS)
    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")
    
    # 1. Identificar todos los candidatos posibles
    candidatos = []
    for idx, row in df.iterrows():
        # Saltamos si ya se le escribió hoy, si es error o está finalizado
        if hoy_str in str(row.get('Fecha_Contacto', '')): continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada", "Error"]: continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        
        # Validación de 23.5 horas para seguimientos
        if row["Estado"] == "Contactado":
            try:
                ultima_fecha = datetime.strptime(str(row['Fecha_Contacto']), "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 84600: continue
            except:
                if str(row['Fecha_Contacto']) != "": continue

        if row["Estado"] == "Contactado" and dia_act < 4:
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({'idx': idx, 'dia': 1})

    # 2. Si no hay candidatos, buscar nuevos leads
    if not candidatos:
        print("📭 Nada pendiente. Buscando nuevos leads...")
        df = buscar_y_agregar_nuevos(df)
        df.to_csv(ARCHIVO_LEADS, index=False) # Guardamos los nuevos hallazgos
        # Recargamos la lista de candidatos tras la búsqueda
        for idx, row in df.iterrows():
            if row["Estado"] == "Nuevo" and not str(row.get('Fecha_Contacto', '')):
                if len(candidatos) < 10: # Límite pequeño por seguridad
                    candidatos.append({'idx': idx, 'dia': 1})

    if not candidatos:
        print("😴 No hay tareas por realizar.")
        return

    # 3. PROCESAMIENTO UNO POR UNO CON GUARDADO INMEDIATO
    print(f"🚀 Procesando {len(candidatos)} envíos programados...")
    
    for i, item in enumerate(candidatos):
        idx = item['idx']
        dia_obj = item['dia']
        row = df.loc[idx]
        
        # Preparar número
        tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        if len(tel) == 9: tel = "56" + tel
        
        msg = obtener_mensaje_secuencia(row["Evento"], row["Ubicacion"], dia_obj)
        
        print(f"[{i+1}/{len(candidatos)}] Enviando a: {row['Evento']}...")
        
        # INTENTO DE ENVÍO
        exito = enviar_mensaje_texto(tel, msg)
        
        # ACTUALIZACIÓN DE ESTADO
        if exito:
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 4 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ✅ Día {dia_obj} enviado con éxito.")
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ❌ Falló el envío. Marcado como Error.")

        # --- GUARDADO INMEDIATO POST-ACCIÓN ---
        df.to_csv(ARCHIVO_LEADS, index=False)
        print(f"   💾 CSV actualizado.")

        # ESPERA DE SEGURIDAD (Solo si faltan más por enviar)
        if i < len(candidatos) - 1:
            espera = random.randint(150, 250)
            print(f"   ⏳ Esperando {espera} segundos para el siguiente...")
            time.sleep(espera)

    print("🏁 Ciclo de trabajo completado.")

if __name__ == "__main__":
    ejecutar_ciclo()

if __name__ == "__main__":
    ejecutar_ciclo()