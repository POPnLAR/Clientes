import pandas as pd
import requests
import os
import random
import time
import unicodedata
import base64
from datetime import datetime
from fpdf import FPDF

# --- CONFIGURACIÓN DESDE GITHUB SECRETS ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"

# --- UTILIDADES ---
def limpiar_acentos(text):
    if not isinstance(text, str): return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

# --- GENERADOR DE PDF (Diseño 1 Sola Hoja) ---
def generar_pdf_diagnostico(nombre_clinica):
    try:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        # Encabezado Blanco (Logo Visible)
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(0, 0, 210, 40, 'F')
        pdf.set_fill_color(0, 102, 204) # Línea azul superior
        pdf.rect(0, 0, 210, 3, 'F')
        
        if os.path.exists("Logo 1.png"):
            pdf.image("Logo 1.png", x=10, y=8, h=22)
        
        pdf.set_text_color(0, 102, 204)
        pdf.set_font("Arial", 'B', 16)
        pdf.set_xy(85, 12)
        pdf.cell(0, 8, "AUDITORIA DE EFICIENCIA", ln=True)
        
        nombre_clean = limpiar_acentos(nombre_clinica)
        pdf.set_y(45)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Arial", 'B', 13)
        pdf.cell(0, 10, f"DIAGNOSTICO: {nombre_clean.upper()}", ln=True)
        
        # Riesgos y Soluciones
        pdf.set_fill_color(255, 240, 240)
        pdf.set_font("Arial", 'B', 10); pdf.set_text_color(180, 0, 0)
        pdf.cell(0, 7, "  PUNTOS CRITICOS DETECTADOS", ln=True, fill=True)
        pdf.set_font("Arial", '', 9); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 5, "    - Gestion manual, Riesgo Legal, No-Show alto e Inventario ciego.", ln=True)
        
        pdf.ln(5)
        pdf.set_fill_color(230, 245, 255)
        pdf.set_font("Arial", 'B', 10); pdf.set_text_color(0, 102, 204)
        pdf.cell(0, 7, "  SOLUCION: ECOSISTEMA GESTIONVITAL PRO", ln=True, fill=True)
        
        modulos = [
            ("Fichas y Firma Digital", "Historias y consentimientos 100% legales."),
            ("WhatsApp Business", "Automatizacion de citas y recordatorios."),
            ("Inventario y Ecommerce", "Control de stock y venta online directa."),
            ("Gestion 360", "Control total de la atencion al paciente.")
        ]
        for tit, desc in modulos:
            pdf.set_font("Arial", 'B', 9); pdf.cell(50, 5, f"  {tit}:", 0)
            pdf.set_font("Arial", '', 9); pdf.cell(0, 5, desc, ln=True)

        pdf.set_y(-20)
        pdf.set_font("Arial", 'I', 8); pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, "GestionVital Pro - Transformacion Digital Medica", align='C')
        
        path = f"auditoria_{nombre_clean[:15].replace(' ', '_')}.pdf"
        pdf.output(path)
        return path
    except Exception as e:
        print(f"Error PDF: {e}")
        return None

# --- COMUNICACIONES ---
def enviar_mensaje_completo(numero, mensaje, path_pdf=None):
    base_url = EVO_URL.strip().rstrip('/')
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    
    text_url = f"{base_url}/message/sendText/{EVO_INSTANCE}"
    text_payload = {"number": numero, "options": {"delay": 1200}, "textMessage": {"text": mensaje}}
    
    try:
        res_text = requests.post(text_url, json=text_payload, headers=headers, timeout=20)
        if res_text.status_code in [200, 201] and path_pdf:
            pdf_url = f"{base_url}/message/sendMedia/{EVO_INSTANCE}"
            with open(path_pdf, "rb") as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
            pdf_payload = {
                "number": numero,
                "mediaMessage": {
                    "mediatype": "document", "fileName": "Auditoria_Digital.pdf",
                    "caption": "Adjunto reporte detallado.", "media": b64
                }
            }
            requests.post(pdf_url, json=pdf_payload, headers=headers, timeout=30)
        return res_text.status_code in [200, 201]
    except: return False

def obtener_mensaje_secuencia(nombre, dia):
    nombre = limpiar_acentos(nombre)
    if dia == 1:
        return f"Hola! 👋 Vi el perfil de *{nombre}* y note potencial para automatizar su gestion. Le prepare una *Auditoria de Eficiencia* de cortesia (adjunta). ¿Le gustaria revisarla?"
    elif dia == 2:
        return f"Hola de nuevo! 👋 Sabia que clinicas como *{nombre}* reducen el No-Show en un 40% con nuestro bot? Tambien eliminamos el papel con Firma Digital. ¿Hablamos 5 min?"
    elif dia == 3:
        return f"Buen dia! 🏥 Tenemos 2 cupos con modulo de *Ecommerce* bonificado en su zona esta semana. ¿Le interesa que *{nombre}* aproveche este beneficio? Saludos!"
    return ""

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = datetime.now()
    hoy_str = ahora.strftime("%d/%m/%Y")
    
    # Límite de envíos por cada vez que despierta el Worker (Ajustable)
    LIMITE_POR_SESION = 5 
    envios_realizados = 0

    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print("Fuera de horario.")
        return 

    if not os.path.exists(ARCHIVO_LEADS): return
    df = pd.read_csv(ARCHIVO_LEADS)
    df['Fecha_Contacto'] = df['Fecha_Contacto'].fillna("")

    # --- INICIO DEL BUCLE DE PROCESAMIENTO ---
    # Buscamos múltiples candidatos que cumplan las reglas
    for idx, row in df.iterrows():
        if envios_realizados >= LIMITE_POR_SESION:
            break # Ya cumplimos la cuota de esta hora

        # Saltamos si ya se contactó HOY
        if hoy_str in str(row['Fecha_Contacto']):
            continue

        procesar = False
        tipo = ""

        # Lógica Seguimiento (>24h)
        if row["Estado"] == "Contactado" and row["Dia_Secuencia"] < 3:
            try:
                fecha_ultimo = datetime.strptime(str(row['Fecha_Contacto']).split()[0], "%d/%m/%Y")
                if (ahora - fecha_ultimo).days >= 1:
                    procesar, tipo = True, "seguimiento"
            except: continue
        
        # Lógica Nuevo
        elif row["Estado"] == "Nuevo":
            procesar, tipo = True, "nuevo"

        if procesar:
            tel = "".join(filter(str.isdigit, str(row["Telefono"])))
            if len(tel) == 9: tel = "56" + tel
            
            # --- ENVÍO ---
            exito = False
            if tipo == "seguimiento":
                proximo_dia = int(row["Dia_Secuencia"]) + 1
                msg = obtener_mensaje_secuencia(row["Evento"], proximo_dia)
                if enviar_mensaje_completo(tel, msg):
                    df.at[idx, "Dia_Secuencia"] = proximo_dia
                    exito = True
            else:
                msg = obtener_mensaje_secuencia(row["Evento"], 1)
                pdf_path = generar_pdf_diagnostico(row["Evento"])
                if enviar_mensaje_completo(tel, msg, pdf_path):
                    df.at[idx, "Estado"] = "Contactado"
                    df.at[idx, "Dia_Secuencia"] = 1
                    if pdf_path and os.path.exists(pdf_path): os.remove(pdf_path)
                    exito = True

            if exito:
                df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
                envios_realizados += 1
                print(f"✅ [{tipo.upper()}] enviado a {row['Evento']}")
                
                # ESPERA HUMANA: Entre 30 y 90 segundos entre cada mensaje del lote
                if envios_realizados < LIMITE_POR_SESION:
                    espera = random.randint(30, 90)
                    print(f"Esperando {espera}s para el siguiente...")
                    time.sleep(espera)

    if envios_realizados > 0:
        df.to_csv(ARCHIVO_LEADS, index=False)
    else:
        print("Nada pendiente por enviar en este ciclo.")

if __name__ == "__main__":
    ejecutar_ciclo()