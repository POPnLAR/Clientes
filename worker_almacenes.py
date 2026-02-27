import pandas as pd
import requests
import os
import random
import time
import unicodedata
import re
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
# Usamos un CSV diferente para no mezclar las bases de datos
ARCHIVO_ALMACENES = "prospeccion_almacenes_pro.csv"

# --- UTILIDADES ---
def obtener_ahora_chile():
    return datetime.utcnow() - timedelta(hours=3)

def aplicar_spintax(texto):
    def reemplazar(match):
        opciones = match.group(1).split('|')
        return random.choice(opciones)
    return re.sub(r'\{([^{}]*)\}', reemplazar, texto)

def limpiar_acentos(text):
    if not isinstance(text, str): return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

# --- BÚSQUEDA DE ALMACENES EN GOOGLE MAPS ---
def buscar_y_agregar_almacenes(df_actual):
    comunas = ["Maipu", "Puente Alto", "La Florida", "San Miguel", "Estacion Central", "Quilicura"]
    zona = random.choice(comunas)
    ahora_cl = obtener_ahora_chile()
    
    print(f"🏪 Buscando Minimarkets y Almacenes en: {zona}...")
    params = {
        "engine": "google_maps",
        "q": f"Minimarket o Almacen en {zona} Chile",
        "api_key": SERP_KEY,
        "num": 20
    }
    
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        nuevos_leads = []
        tels_en_base = set(df_actual['Telefono'].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist())
        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0

        for place in results:
            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not raw_tel or len(raw_tel) < 8: continue
            
            if raw_tel[-9:] not in tels_en_base:
                ultimo_id += 1
                nuevos_leads.append({
                    "Id": int(ultimo_id), "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                    "Hora": ahora_cl.strftime("%H:%M"), "Evento": place.get("title", "Almacen"),
                    "Ministerio": "App Almacen", "Ubicacion": zona, "Estado": "Nuevo",
                    "Telefono": raw_tel, "Dia_Secuencia": 0, "Fecha_Contacto": ""
                })
                tels_en_base.add(raw_tel[-9:])
        
        if nuevos_leads: return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    except Exception as e: print(f"❌ Error búsqueda: {e}")
    return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    if not EVO_URL or not EVO_TOKEN: return False
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    try:
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", 
                     json={"number": numero, "presence": "composing"}, headers=headers)
        time.sleep(random.randint(15, 30)) # Simular escritura
        
        payload = {
            "number": numero, 
            "options": {"delay": 2000, "presence": "composing"}, 
            "textMessage": {"text": mensaje}
        }
        res = requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", json=payload, headers=headers, timeout=20)
        return res.status_code in [200, 201]
    except: return False

def obtener_mensaje_almacen(nombre, ubicacion, dia):
    nombre = limpiar_acentos(nombre)
    zona = ubicacion if ubicacion else "el sector"
    
    if dia == 1:
        msg = ("{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Mi nombre es Rodrigo. Paso seguido por {zona} y veo que en **{nombre}** {tienen mucha variedad|siempre tienen movimiento}.\n\n"
               "Les escribo porque desarrollamos una **app chilena** para dueños de almacenes que quieren {controlar su stock|ver sus ventas diarias|ordenar las cuentas} desde el celular de forma fácil. ✨\n\n"
               "{¿Les gustaría|¿Les interesa} que les envíe un videito de 1 minuto para que vean cómo les puede ayudar a ganar tiempo? ¡Saludos!")
    elif dia == 2:
        msg = ("{Hola de nuevo|Hola nuevamente} 👋 Solo les escribía para comentarles que nuestra app también ayuda a llevar el control de los **fiados**, para que no se pierda ninguna cuenta en el cuaderno. 📋 ¿Les tinca conversar un minutito?")
    else: return ""
    
    return aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = obtener_ahora_chile()
    
    # Horario Almacenero: 10 AM a 19 PM (Lunes a Sábado)
    if ahora.weekday() > 5 or not (10 <= ahora.hour <= 19): 
        print(f"🕒 Fuera de horario para almacenes.")
        return 

    if not os.path.exists(ARCHIVO_ALMACENES):
        df = pd.DataFrame(columns=["Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado","Telefono","Dia_Secuencia","Fecha_Contacto"])
    else:
        df = pd.read_csv(ARCHIVO_ALMACENES)

    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")
    
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get('Fecha_Contacto', '')): continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Error", "Cita Agendada"]: continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        if row["Estado"] == "Contactado":
            try:
                ultima_fecha = datetime.strptime(str(row['Fecha_Contacto']), "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 90000: continue
            except: pass

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
        raw_tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        tel_final = "56" + raw_tel if (len(raw_tel) == 9 and raw_tel.startswith("9")) else raw_tel

        msg = obtener_mensaje_almacen(row["Evento"], row["Ubicacion"], dia_obj)
        if not msg: continue

        if enviar_mensaje_texto(tel_final, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 2 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ✅ Día {dia_obj} enviado a {row['Evento']}.")
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")

        df.to_csv(ARCHIVO_ALMACENES, index=False)
        if i < len(candidatos) - 1:
            time.sleep(random.randint(300, 600)) # Pausas de 5-10 minutos

if __name__ == "__main__":
    ejecutar_ciclo()