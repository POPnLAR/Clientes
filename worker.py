import pandas as pd
import requests
import os
import random
import time
import unicodedata
import re
from bs4 import BeautifulSoup
from datetime import datetime

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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code != 200:
            return ""

        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', response.text)
        filtrados = [e for e in emails if not e.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))]
        
        if filtrados:
            prioritarios = [e for e in filtrados if any(p in e.lower() for p in ['contacto', 'info', 'administracion', 'ventas', 'hola'])]
            return prioritarios[0].lower() if prioritarios else filtrados[0].lower()
    except:
        pass
    return ""

# --- BÚSQUEDA AUTOMÁTICA ESCALADA ---
def buscar_y_agregar_nuevos(df_actual):
    comunas = ["Las Condes", "Providencia", "Vitacura", "Lo Barnechea", "Ñuñoa", "La Reina"]
    zona_objetivo = random.choice(comunas)
    
    print(f"🔍 Prospección estratégica iniciada en: {zona_objetivo}...")
    
    params = {
        "engine": "google_maps",
        "q": f"Clinica Estetica {zona_objetivo} Chile",
        "type": "search",
        "api_key": SERP_KEY,
        "num": 20 # Aumentamos el número de resultados solicitados
    }
    
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        
        nuevos_leads = []
        tels_en_base = set(df_actual['Telefono'].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist())
        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0

        for place in results:
            tiene_web = place.get("website")
            if not tiene_web: continue 

            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not raw_tel or len(raw_tel) < 8: continue
            
            if raw_tel[-9:] not in tels_en_base:
                print(f"🌐 Analizando sitio web: {tiene_web}...")
                email_hallado = buscar_email_en_web(tiene_web)
                
                ultimo_id += 1
                nuevo = {
                    "Id": int(ultimo_id),
                    "Fecha": datetime.now().strftime("%d/%m/%Y"),
                    "Hora": datetime.now().strftime("%H:%M"),
                    "Evento": place.get("title", "Clinica"),
                    "Ministerio": "Prospeccion Automatica",
                    "Ubicacion": zona_objetivo,
                    "Estado": "Nuevo",
                    "Telefono": raw_tel,
                    "Email": email_hallado,
                    "Email_Enviado": "No",
                    "Dia_Secuencia": 0,
                    "Fecha_Contacto": ""
                }
                nuevos_leads.append(nuevo)
                tels_en_base.add(raw_tel[-9:])
                if email_hallado:
                    print(f"📧 Email detectado: {email_hallado}")
                time.sleep(1)
        
        if nuevos_leads:
            df_nuevos = pd.DataFrame(nuevos_leads)
            print(f"✨ Incorporados {len(nuevos_leads)} leads de alta calidad.")
            return pd.concat([df_actual, df_nuevos], ignore_index=True)
        return df_actual
            
    except Exception as e:
        print(f"❌ Error en búsqueda: {str(e)}")
        return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    
    try:
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", 
                      json={"number": numero, "presence": "composing"}, headers=headers)
        time.sleep(random.randint(5, 10))

        text_url = f"{base_url}/message/sendText/{EVO_INSTANCE}"
        text_payload = {
            "number": numero, 
            "options": {"delay": 1200, "presence": "composing"}, 
            "textMessage": {"text": mensaje}
        }
        res_text = requests.post(text_url, json=text_payload, headers=headers, timeout=20)
        return res_text.status_code in [200, 201]
    except: return False

def obtener_mensaje_secuencia(nombre, ubicacion, dia):
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "su zona"
    
    if dia == 1:
        return (f"Estimados, un gusto saludarles. 👋 Mi nombre es Rodrigo y represento a **GestiónVital**, consultora especializada en la profesionalización de centros estéticos en {zona}.\n\n"
                f"Hemos realizado un análisis preventivo sobre la presencia operativa de *{nombre}* y detectamos tres brechas críticas:\n\n"
                f"1️⃣ **Fuga por Latencia:** Pacientes que no concretan por demora en respuesta.\n"
                f"2️⃣ **Vulnerabilidad Legal:** Gestión manual de fichas y consentimientos.\n"
                f"3️⃣ **Mermas en Insumos:** Falta de control de stock digital.\n\n"
                f"¿Podríamos coordinar una sesión breve de 5 minutos para explicarles cómo optimizar estos puntos?")
    elif dia == 2:
        return (f"Hola de nuevo. 👋 En **GestiónVital** estandarizamos negocios para que escalen sin depender de la presencia física del dueño.\n\n"
                f"¿Sabía que digitalizar la operación de *{nombre}* puede elevar su rentabilidad neta en un 20%? Me gustaría mostrarle nuestro modelo de gestión para clínicas en {zona}.")
    elif dia == 3:
        return (f"Buen día. 🏥 Estamos seleccionando a la clínica referente de {zona} para nuestro programa de **Transformación Digital 2026**.\n\n"
                f"Buscamos un perfil como el de *{nombre}* para establecer un estándar de alta dirección. ¿Conversamos hoy para evaluar si su visión se alinea con este nivel profesional?")
    elif dia == 4:
        return (f"Estimados en *{nombre}*, entiendo que su agenda debe estar a tope. 👋\n\n"
                "Para no ser invasivo, les dejo mi contacto directo. Si en el futuro deciden dar el salto a una **Operación 360**, estaré encantado de ayudarles. ¡Mucho éxito!")
    return ""

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = datetime.now()
    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print("Fuera de horario de envío (Lunes-Sábado 09:00-20:00)")
        return 

    columnas = ["Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado","Telefono","Email","Email_Enviado","Dia_Secuencia","Fecha_Contacto"]
    
    if not os.path.exists(ARCHIVO_LEADS):
        df = pd.DataFrame(columns=columnas)
    else:
        df = pd.read_csv(ARCHIVO_LEADS)
        for col in columnas:
            if col not in df.columns: 
                df[col] = "No" if col == "Email_Enviado" else ("" if col != "Dia_Secuencia" else 0)
    
    hoy = ahora.strftime("%d/%m/%Y")
    candidatos = []
    
    for idx, row in df.iterrows():
        if hoy in str(row['Fecha_Contacto']): continue
        dia_act = int(row.get("Dia_Secuencia", 0))
        
        if row["Estado"] == "Contactado" and 1 <= dia_act < 4:
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({'idx': idx, 'dia': 1})

    # Si no hay trabajo, buscar sangre nueva (Aumentado a 20)
    if not candidatos:
        df = buscar_y_agregar_nuevos(df)
        for idx, row in df.iterrows():
            if row["Estado"] == "Nuevo" and len(candidatos) < 20:
                candidatos.append({'idx': idx, 'dia': 1})

    # Procesar lote de 20 para mayor tracción comercial
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
            print(f"✅ Mensaje Día {dia_obj} enviado a {row['Evento']}")
            df.to_csv(ARCHIVO_LEADS, index=False)
            time.sleep(random.randint(120, 300)) # Delay de 2 a 5 min

if __name__ == "__main__":
    ejecutar_ciclo()