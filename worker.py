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

# --- EXTRACTOR DE CORREOS ---
def buscar_email_en_web(url):
    if not url or not url.startswith("http"): return ""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=12)
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
    ahora_cl = obtener_ahora_chile()
    print(f"🔍 Buscando nuevos leads en: {zona_objetivo}...")
    params = {"engine": "google_maps", "q": f"Clinica Estetica {zona_objetivo} Chile", "api_key": SERP_KEY, "num": 15}
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        nuevos_leads = []
        tels_en_base = set(df_actual['Telefono'].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist())
        ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0
        for place in results:
            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not place.get("website") or not raw_tel or len(raw_tel) < 8: continue
            if raw_tel[-9:] not in tels_en_base:
                ultimo_id += 1
                nuevos_leads.append({
                    "Id": int(ultimo_id), "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                    "Hora": ahora_cl.strftime("%H:%M"), "Evento": place.get("title", "Clinica"),
                    "Ministerio": "Prospeccion Automatica", "Ubicacion": zona_objetivo, "Estado": "Nuevo",
                    "Telefono": raw_tel, "Email": buscar_email_en_web(place.get("website")), 
                    "Email_Enviado": "No", "Dia_Secuencia": 0, "Fecha_Contacto": ""
                })
                tels_en_base.add(raw_tel[-9:])
        if nuevos_leads: return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    except Exception as e: print(f"❌ Error búsqueda: {e}")
    return df_actual

# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    if not mensaje or len(mensaje.strip()) < 10: return False
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    try:
        # 1. Simular Presencia "Componiendo" (Typing)
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", 
                     json={"number": numero, "presence": "composing"}, headers=headers)
        
        # Simular tiempo de escritura humana (15-35 seg)
        tiempo_escritura = random.randint(15, 35)
        time.sleep(tiempo_escritura)
        
        payload = {
            "number": numero, 
            "options": {"delay": 2000, "presence": "composing"}, 
            "textMessage": {"text": mensaje}
        }
        res = requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", json=payload, headers=headers, timeout=20)
        return res.status_code in [200, 201]
    except: return False

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
               "{¿Tendrán 5 minutitos|¿Tendrán un espacio|¿Podríamos conversar brevemente} esta semana {para conocernos|de forma relajada}? Me encantaría conocerles.")
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
        df = buscar_y_agregar_nuevos(df)
        df.to_csv(ARCHIVO_LEADS, index=False)
        return

    print(f"🚀 Procesando ráfaga de {len(candidatos)} envíos (Hora Chile: {ahora.strftime('%H:%M')})...")
    
    for i, item in enumerate(candidatos):
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]
        
        raw_tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        tel_final = "56" + raw_tel if (len(raw_tel) == 9 and raw_tel.startswith("9")) else raw_tel

        msg = obtener_mensaje_secuencia(row["Evento"], row["Ubicacion"], dia_obj)
        if not msg: continue

        print(f"[{i+1}/{len(candidatos)}] Enviando a: {row['Evento']}...")
        
        if enviar_mensaje_texto(tel_final, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 4 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ✅ Día {dia_obj} enviado.")
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"   ❌ Fallo técnico.")

        df.to_csv(ARCHIVO_LEADS, index=False)

        # Pausa larga entre mensajes (4 a 8 minutos)
        if i < len(candidatos) - 1:
            espera = random.randint(240, 480)
            print(f"   ⏳ Pausa de seguridad: {espera} seg...")
            time.sleep(espera)

    print("🏁 Ciclo completado.")

if __name__ == "__main__":
    ejecutar_ciclo()