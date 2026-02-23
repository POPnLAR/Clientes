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

# --- NUEVA FUNCIÓN: BÚSQUEDA AUTOMÁTICA ---
def buscar_y_agregar_nuevos(df_actual):
    print("🔍 No hay envíos pendientes. Buscando nuevos prospectos en Google Maps...")
    
    params = {
        "engine": "google_maps",
        "q": "Clinica Estetica Santiago Chile",
        "type": "search",
        "api_key": SERP_KEY
    }
    
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=30)
        results = response.json().get("local_results", [])
        
        nuevos_leads = []
        # Normalizamos teléfonos existentes para comparar
        tels_en_base = set(df_actual['Telefono'].astype(str).str.replace(".0", "", regex=False).str[-9:].tolist())
        
        # Aseguramos que el ID sea entero
        try:
            ultimo_id = int(df_actual['Id'].max()) if not df_actual.empty else 0
        except:
            ultimo_id = 0

        for place in results:
            raw_tel = str(place.get("phone", "")).replace(" ", "").replace("-", "")
            if not raw_tel or len(raw_tel) < 8: continue
            
            # Validar si ya existe
            if raw_tel[-9:] not in tels_en_base:
                ultimo_id += 1
                nuevo = {
                    "Id": int(ultimo_id),
                    "Fecha": datetime.now().strftime("%d/%m/%Y"),
                    "Hora": datetime.now().strftime("%H:%M"),
                    "Hora fin": "",
                    "Evento": place.get("title", "Clinica"),
                    "Ministerio": "Prospeccion Automatica",
                    "Ubicacion": place.get("address", "Santiago"),
                    "Estado": "Nuevo",
                    "Telefono": raw_tel,
                    "Dia_Secuencia": 0,
                    "Fecha_Contacto": ""
                }
                nuevos_leads.append(nuevo)
                # Evitar duplicados dentro del mismo ciclo de búsqueda
                tels_en_base.add(raw_tel[-9:])
        
        if nuevos_leads:
            print(f"✨ Se encontraron {len(nuevos_leads)} nuevos leads.")
            return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
        else:
            print("⚠️ Búsqueda completada, pero no hay resultados nuevos.")
            return df_actual
            
    except Exception as e:
        # Error corregido: Convertimos e a string explícitamente
        print("❌ Error en búsqueda: " + str(e))
        return df_actual

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

# --- NUEVOS MENSAJES DE ALTO IMPACTO (PUNTO 1) ---
def obtener_mensaje_secuencia(nombre, dia):
    nombre = limpiar_acentos(nombre)
    
    if dia == 1:
        # ESTRATEGIA: Presentación Corporativa + Auditoría Externa
        return (f"Estimados, un gusto saludarles. 👋 Mi nombre es Rodrigo y soy parte del equipo de **GestiónVital**, firma consultora especializada en la profesionalización y transformación digital de centros médicos estéticos.\n\n"
                f"Hemos realizado una auditoría externa preventiva sobre el ecosistema operativo de *{nombre}* y detectamos tres brechas críticas que están comprometiendo su escalabilidad:\n\n"
                f"1️⃣ **Fuga por Latencia Operativa:** Pérdida de pacientes en el embudo de conversión por falta de procesos automatizados de respuesta.\n"
                f"2️⃣ **Vulnerabilidad Normativa:** Ausencia de una infraestructura digital robusta para el manejo de Fichas Clínicas y Consentimientos Legales.\n"
                f"3️⃣ **Ineficiencia en el Control de Activos:** Mermas no contabilizadas en insumos críticos debido a una gestión de inventario manual.\n\n"
                "Nuestra metodología permite integrar toda la operación en una arquitectura 360. ¿Podríamos coordinar una breve sesión diagnóstica?")
    
    elif dia == 2:
        # ESTRATEGIA: Visión de Negocio y Rentabilidad
        return (f"Hola de nuevo. 👋 En **GestiónVital** no solo implementamos tecnología; estandarizamos negocios. Una clínica profesionalizada permite al dueño recuperar el control total sin depender de la presencia física constante.\n\n"
                f"¿Sabía que la digitalización integral de *{nombre}* puede incrementar su margen neto operativo en un 20% al eliminar procesos redundantes? ¿Le gustaría conocer nuestro modelo de implementación?")
    
    elif dia == 3:
        # ESTRATEGIA: Selección de Casos de Éxito
        return (f"Buen día. 🏥 Actualmente estamos seleccionando a la clínica referente de su zona para liderar nuestro programa de **Transformación Digital 2026**.\n\n"
                f"Buscamos un perfil como el de *{nombre}* para establecer un estándar de alta dirección en la región. ¿Conversamos hoy para evaluar si su visión se alinea con este nivel de gestión profesional?")
    
    return ""
# --- NUEVO DISEÑO DE PDF VISUAL (PUNTO 3) ---
def generar_pdf_diagnostico(nombre_clinica):
    try:
        pdf = FPDF()
        pdf.add_page()
        
        # Header Estilo Moderno
        pdf.set_fill_color(15, 23, 42) # Azul Slate 900
        pdf.rect(0, 0, 210, 50, 'F')
        
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 22)
        pdf.set_xy(10, 15)
        pdf.cell(0, 10, "AUDITORÍA DE EFICIENCIA DIGITAL", ln=True)
        
        pdf.set_font("Arial", '', 12)
        pdf.cell(0, 10, f"Preparado exclusivamente para: {limpiar_acentos(nombre_clinica).upper()}", ln=True)
        
        # Cuerpo del PDF
        pdf.set_y(60)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "DIAGNÓSTICO DE IMPACTO OPERATIVO", ln=True)
        pdf.ln(5)
        
        # Bloque de puntos clave con iconos simulados
        puntos = [
            ("Pérdida por Citas", "Se estima un 30% de inasistencia por falta de recordatorios auto."),
            ("Fuga de Tiempo", "2 horas diarias perdidas en gestión manual de WhatsApp."),
            ("Riesgo Legal", "Necesidad urgente de Fichas Clínicas con Firma Digital legal."),
            ("Venta Directa", "Ausencia de Ecommerce para venta de tratamientos 24/7.")
        ]
        
        for titulo, desc in puntos:
            pdf.set_font("Arial", 'B', 11)
            pdf.set_text_color(59, 130, 246) # Azul Eléctrico
            pdf.cell(0, 7, f"> {titulo}", ln=True)
            pdf.set_text_color(71, 85, 105) # Gris Slate
            pdf.set_font("Arial", '', 10)
            pdf.multi_cell(0, 6, desc)
            pdf.ln(3)
            
        # Footer / Llamado a la acción
        pdf.set_y(-40)
        pdf.set_fill_color(248, 250, 252)
        pdf.rect(0, 250, 210, 47, 'F')
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Arial", 'I', 10)
        pdf.set_y(-30)
        pdf.cell(0, 10, "Este reporte es una estimación basada en estándares de la industria estetica 2026.", align='C', ln=True)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "GestionVital Pro - Optimizando el futuro de su clínica.", align='C', ln=True)
        
        nombre_clean = limpiar_acentos(nombre_clinica)
        path = f"auditoria_{nombre_clean[:15].replace(' ', '_')}.pdf"
        pdf.output(path)
        return path
    except Exception as e:
        print(f"Error generando PDF: {e}")
        return None

# --- CICLO PRINCIPAL ---
def ejecutar_ciclo():
    ahora = datetime.now()
    hoy_str = ahora.strftime("%d/%m/%Y")
    LIMITE_POR_SESION = 5 
    envios_realizados = 0

    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print(f"Fuera de horario: {ahora.strftime('%A')}")
        return 

    if not os.path.exists(ARCHIVO_LEADS):
        df = pd.DataFrame(columns=["Id", "Fecha", "Hora", "Hora fin", "Evento", "Ministerio", "Ubicacion", "Estado", "Telefono", "Dia_Secuencia", "Fecha_Contacto"])
    else:
        df = pd.read_csv(ARCHIVO_LEADS)

    df['Fecha_Contacto'] = df['Fecha_Contacto'].fillna("").astype(str)
    df['Estado'] = df['Estado'].fillna("Nuevo").astype(str).str.strip()
    df['Dia_Secuencia'] = pd.to_numeric(df['Dia_Secuencia'], errors='coerce').fillna(0)

    # --- 1. IDENTIFICAR CANDIDATOS EXISTENTES ---
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in row['Fecha_Contacto']: continue
        
        if row["Estado"].lower() == "contactado" and 1 <= row["Dia_Secuencia"] < 3:
            try:
                fecha_ultimo = datetime.strptime(row['Fecha_Contacto'].split()[0], "%d/%m/%Y")
                if (ahora - fecha_ultimo).days >= 1:
                    candidatos.append({'idx': idx, 'tipo': 'seguimiento', 'dia': int(row["Dia_Secuencia"]) + 1})
            except: continue
        elif row["Estado"].lower() == "nuevo":
            candidatos.append({'idx': idx, 'tipo': 'nuevo', 'dia': 1})

    # --- 2. SI NO HAY CANDIDATOS, BUSCAR NUEVOS CLIENTES ---
    if not candidatos:
        df = buscar_y_agregar_nuevos(df)
        # Recargar candidatos tras la búsqueda
        for idx, row in df.iterrows():
            if row["Estado"] == "Nuevo" and len(candidatos) < LIMITE_POR_SESION:
                candidatos.append({'idx': idx, 'tipo': 'nuevo', 'dia': 1})

    if not candidatos:
        print("Sin leads pendientes y no se encontraron nuevos en la búsqueda.")
        df.to_csv(ARCHIVO_LEADS, index=False)
        return

    # --- 3. PROCESAR ENVÍOS ---
    lote = candidatos[:LIMITE_POR_SESION]
    for item in lote:
        idx, tipo, dia_obj = item['idx'], item['tipo'], item['dia']
        row = df.loc[idx]
        
        tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        if len(tel) == 9: tel = "56" + tel
        
        msg = obtener_mensaje_secuencia(row["Evento"], dia_obj)
        pdf = generar_pdf_diagnostico(row["Evento"]) if dia_obj == 1 else None
        
        if enviar_mensaje_completo(tel, msg, pdf):
            df.at[idx, "Estado"] = "Contactado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            envios_realizados += 1
            print(f"✅ {tipo.upper()} (Día {dia_obj}) enviado a {row['Evento']}")
            if pdf and os.path.exists(pdf): os.remove(pdf)
            if envios_realizados < len(lote): time.sleep(random.randint(45, 90))

    if envios_realizados > 0 or len(df) > 0:
        df.to_csv(ARCHIVO_LEADS, index=False)

if __name__ == "__main__":
    ejecutar_ciclo()