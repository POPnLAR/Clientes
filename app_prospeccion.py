import streamlit as st
import pandas as pd
import requests
import os
import random
import time
import base64
import unicodedata
from datetime import datetime, timedelta
from fpdf import FPDF

# --- CONFIGURACIÓN ESTRUCTURA DE DATOS ---
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"
COLUMNAS_REQUERIDAS = ["Id", "Fecha", "Hora", "Evento", "Ministerio", "Ubicacion", "Estado", "Propuesta", "Motivo", "Telefono", "Fecha_Contacto", "Reintentos", "Notas", "Dia_Secuencia"]

@st.cache_data(ttl=2)
def cargar_base_segura():
    if os.path.exists(ARCHIVO_LEADS):
        df = pd.read_csv(ARCHIVO_LEADS)
        for col in COLUMNAS_REQUERIDAS:
            if col not in df.columns:
                if col == "Dia_Secuencia": df[col] = 1
                elif col == "Notas": df[col] = "Sin notas"
                else: df[col] = 0 if col == "Reintentos" else "Sin contacto"
        return df
    return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)

# --- GENERADOR DE DIAGNÓSTICO PDF: VERSIÓN CORPORATIVA (FONDO CLARO PARA LOGO) ---
# --- GENERADOR DE DIAGNÓSTICO PDF: VERSIÓN UNA SOLA HOJA ---
def generar_pdf_diagnostico(nombre_clinica, tiene_web):
    try:
        # Desactivamos el salto de página automático muy cerca del borde para evitar la hoja en blanco
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15) 
        pdf.add_page()
        
        # 1. Encabezado Corporativo (Fondo Blanco para Logo)
        pdf.set_fill_color(255, 255, 255) 
        pdf.rect(0, 0, 210, 40, 'F')
        
        # Línea decorativa superior azul
        pdf.set_fill_color(0, 102, 204)
        pdf.rect(0, 0, 210, 3, 'F')
        
        # Logo
        if os.path.exists("Logo 1.png"):
            pdf.image("Logo 1.png", x=10, y=8, h=22)
        
        # Texto encabezado
        pdf.set_text_color(0, 102, 204)
        pdf.set_font("Arial", 'B', 16)
        pdf.set_xy(85, 12)
        pdf.cell(0, 8, "AUDITORIA DE EFICIENCIA", ln=True, align='L')
        
        pdf.set_font("Arial", '', 10)
        pdf.set_text_color(100, 100, 100)
        pdf.set_xy(85, 20)
        pdf.cell(0, 8, "Ecosistema de Gestion de Salud GestionVital Pro", ln=True, align='L')
        
        pdf.set_y(45) # Bajamos el cursor para el contenido
        
        # Limpieza de nombre
        nombre_raw = str(nombre_clinica)
        nombre_limpio_txt = "".join(c for c in unicodedata.normalize('NFD', nombre_raw) if unicodedata.category(c) != 'Mn')
        
        # 2. Análisis
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Arial", 'B', 13)
        pdf.cell(0, 10, f"DIAGNOSTICO: {nombre_limpio_txt.upper()}", ln=True)
        
        # Cuadro de Riesgos (Compactado)
        pdf.set_fill_color(255, 240, 240)
        pdf.set_text_color(180, 0, 0)
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 7, "  PUNTOS CRITICOS DETECTADOS", ln=True, fill=True)
        pdf.set_font("Arial", '', 9)
        riesgos = [
            "- Gestion manual de citas y perdida de tiempo administrativo.",
            "- Riesgo legal por falta de Firma de Consentimiento Digital.",
            "- Inasistencias (No-Show) por falta de avisos via WhatsApp.",
            "- Fuga de stock por Inventario no automatizado."
        ]
        for riesgo in riesgos:
            pdf.cell(0, 5, f"    {riesgo}", ln=True)
        
        pdf.ln(4) # Espacio reducido
        
        # 3. Solución Full (Compactado)
        pdf.set_fill_color(230, 245, 255)
        pdf.set_text_color(0, 102, 204)
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(0, 8, "  SOLUCION: ECOSISTEMA GESTIONVITAL PRO", ln=True, fill=True)
        
        pdf.set_font("Arial", '', 9)
        pdf.set_text_color(0, 0, 0)
        
        servicios = [
            ("Fichas Medicas Pro:", "Historias clinicas digitales y seguras."),
            ("Firma Digital:", "Consentimientos informados desde tablets."),
            ("WhatsApp Business:", "Notificaciones y recordatorios automaticos."),
            ("Gestion 360:", "Control de atenciones de principio a fin."),
            ("Inventario Automatico:", "Control de insumos vinculado a prestaciones."),
            ("Fidelizacion:", "Herramientas de marketing post-atencion."),
            ("Ecommerce y Pagos:", "Venta online con recaudacion directa."),
            ("Agenda:", "Reserva online 24/7.")
        ]
        
        for titulo, desc in servicios:
            pdf.set_font("Arial", 'B', 9)
            pdf.cell(50, 5, f"  {titulo}", 0)
            pdf.set_font("Arial", '', 9)
            pdf.cell(0, 5, desc, ln=True)

        pdf.ln(6)
        
        # 4. Banner de Cierre (CTA)
        pdf.set_fill_color(0, 153, 76)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 11)
        pdf.cell(0, 10, "Para mas información visitanos en www.gestionvitalpro.cl", ln=True, fill=True, align='C')

        # 5. Pie de Página (Posicionamiento fijo para evitar saltos)
        pdf.set_y(-20)
        pdf.set_font("Arial", 'I', 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, "GestionVital Pro - La plataforma lider en gestion medica digital.", align='C', ln=True)
        
        # Nombre de archivo
        nombre_file = "".join([c for c in nombre_limpio_txt if c.isalnum() or c==' ']).replace(' ', '_')[:20]
        nombre_archivo = f"auditoria_{nombre_file}.pdf"
        
        # Guardado con manejo de errores
        try:
            pdf.output(nombre_archivo)
        except:
            nombre_archivo = f"auditoria_{nombre_file}_{random.randint(100,999)}.pdf"
            pdf.output(nombre_archivo)
            
        return nombre_archivo
    except Exception as e:
        st.error(f"Error en PDF: {e}")
        return None

# --- MODIFICACIÓN EN LA FUNCIÓN DE ENVÍO PARA EVITAR EL 'NONETYPE' ---
def ejecutar_workflow_automatico(serp_key, target, zona, evo_url, evo_token, evo_instance, limite_max):
    # ... (resto del código igual hasta la parte del envío)
    
    path_pdf = generar_pdf_diagnostico(item.get("title"), item.get("website"))
    
    if enviar_whatsapp_evolution(evo_url, evo_token, evo_instance, tel_clean, msg):
        # Solo intentamos enviar el PDF si la ruta no es None
        if path_pdf is not None:
            enviar_pdf_evolution(evo_url, evo_token, evo_instance, tel_clean, path_pdf)
        else:
            st.warning("No se pudo adjuntar el PDF porque falló su generación.")

# --- MOTOR DE MENSAJES ---
def obtener_mensaje_secuencia(item, dia):
    nombre = item.get("Evento") or item.get("title")
    # Limpiamos el nombre para que no se vea con códigos raros en el mensaje
    nombre_limpio = "".join(c for c in unicodedata.normalize('NFD', str(nombre)) if unicodedata.category(c) != 'Mn')

    if dia == 1:
        return (
            f"Hola! 👋 Vi el perfil de *{nombre_limpio}* y me llamó la atención su potencial. \n\n"
            f"Me tomé la libertad de prepararles una *Auditoría de Eficiencia Digital* de cortesía (se la adjunto en el siguiente mensaje). 📈\n\n"
            f"Detecté que podrían automatizar gran parte de su operación, desde la firma de consentimientos hasta el control de stock, ahorrando horas de trabajo manual. ¿Le gustaría que revisemos el informe?"
        )
    
    elif dia == 2:
        return (
            f"Hola de nuevo! 👋 Solo quería compartirle un dato: las clínicas que usan el ecosistema de *GestiónVital Pro* reducen el ausentismo de pacientes en un 40% gracias a nuestro Bot de WhatsApp especializado. 🤖\n\n"
            f"¿Han pensado en digitalizar las fichas y la firma de consentimientos para eliminar el papel? Me encantaría mostrarle cómo funciona nuestro panel 360. ¿Tendrán 5 minutos?"
        )
    
    elif dia == 3:
        return (
            f"Buen día! 🏥 Para no quitarle más tiempo, le comento que esta semana estamos liberando 2 implementaciones con el módulo de *Ecommerce y Fidelización* bonificado para centros en su zona. \n\n"
            f"Es la herramienta ideal para que *{nombre_limpio}* no solo gestione pacientes, sino que aumente su rentabilidad en piloto automático. ¿Le interesa que le envíe el detalle del beneficio? Saludos!"
        )
    
    return "Fin de secuencia"

def enviar_whatsapp_evolution(instance_url, instance_token, instance_name, remote_jid, message):
    url = f"{instance_url}/message/sendText/{instance_name}"
    headers = {"Content-Type": "application/json", "apikey": instance_token}
    payload = {
        "number": remote_jid,
        "options": {"delay": 2000, "presence": "composing", "linkPreview": True},
        "textMessage": {"text": message}
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.status_code in [200, 201]
    except: return False

def enviar_pdf_evolution(instance_url, instance_token, instance_name, remote_jid, path_pdf):
    url = f"{instance_url}/message/sendMedia/{instance_name}"
    headers = {"Content-Type": "application/json", "apikey": instance_token}
    
    try:
        with open(path_pdf, "rb") as f:
            base64_pdf = base64.b64encode(f.read()).decode('utf-8')
        
        payload = {
            "number": remote_jid,
            "mediaMessage": {
                "mediatype": "document",
                "fileName": "Auditoria_GestionVital_Pro.pdf",
                "caption": "Adjunto Auditoria Digital de Cortesía",
                "media": base64_pdf
            }
        }
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        return response.status_code in [200, 201]
    except Exception as e:
        st.error(f"Error enviando PDF: {e}")
        return False

# --- MOTOR DE AUTOMATIZACIÓN ---
def obtener_conteo_diario():
    df = cargar_base_segura()
    # Usamos el formato dd/mm/yyyy solicitado en las instrucciones
    hoy = datetime.now().strftime("%d/%m/%Y")
    contactos_hoy = df[df['Fecha_Contacto'].str.contains(hoy, na=False)]
    return len(contactos_hoy)

def ejecutar_workflow_automatico(serp_key, target, zona, evo_url, evo_token, evo_instance, limite_max):
    st.toast("🤖 [Auto-Pilot] Iniciando secuencia...", icon="🔄")
    if obtener_conteo_diario() >= limite_max:
        st.error("Límite diario alcanzado.")
        return

    params = {"engine": "google_maps", "q": f"{target} en {zona}", "api_key": serp_key}
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=20)
        results = response.json().get("local_results", [])
    except: return
    
    df_actual = cargar_base_segura()
    ids_viejos = set(df_actual["Id"].astype(str).tolist())
    
    for item in results:
        if obtener_conteo_diario() >= limite_max: break
        pid = item.get("place_id")
        telefono = item.get("phone")
        
        if pid in ids_viejos or not telefono or telefono == "No disponible":
            continue
            
        msg = obtener_mensaje_secuencia(item, 1)
        # El PDF ahora incluye el Logo 1.png automáticamente
        path_pdf = generar_pdf_diagnostico(item.get("title"), item.get("website"))
        
        tel_clean = "".join(filter(str.isdigit, str(telefono)))
        if len(tel_clean) == 9: tel_clean = "56" + tel_clean
        
        # Envío de Texto + PDF Corporativo
        if enviar_whatsapp_evolution(evo_url, evo_token, evo_instance, tel_clean, msg):
            if path_pdf:
                enviar_pdf_evolution(evo_url, evo_token, evo_instance, tel_clean, path_pdf)
            
            nuevo_lead = {
                "Id": pid, "Fecha": datetime.now().strftime("%d/%m/%Y"), "Hora": datetime.now().strftime("%H:%M"),
                "Evento": item.get("title"), "Ministerio": target, "Ubicacion": item.get("address"),
                "Estado": "Contactado", "Propuesta": msg, "Motivo": "Día 1 - Secuencia",
                "Telefono": telefono, "Fecha_Contacto": datetime.now().strftime("%d/%m/%Y %H:%M"), 
                "Reintentos": 1, "Notas": "PDF con Logo Enviado", "Dia_Secuencia": 1
            }
            df_actual = pd.concat([df_actual, pd.DataFrame([nuevo_lead])], ignore_index=True)
            df_actual.to_csv(ARCHIVO_LEADS, index=False)
            # Respetamos tiempos de seguridad para evitar bloqueos
            time.sleep(random.randint(45, 80))
        
    st.session_state.ultima_ejecucion = datetime.now()

# --- INTERFAZ ---
st.set_page_config(page_title="GestiónVital Pro - Automation", layout="wide")
st.title("🚀 GestiónVital Pro: Captación Inteligente")

if 'ultima_ejecucion' not in st.session_state:
    st.session_state.ultima_ejecucion = None

df_actual = cargar_base_segura()
conteo_hoy = obtener_conteo_diario()

with st.sidebar:
    st.header("⚙️ Configuración")
    evo_url = st.text_input("URL Evolution API", placeholder="https://api.tudominio.com")
    evo_token = st.text_input("API Key Evolution", type="password")
    evo_instance = st.text_input("Instancia", value="Main")
    serp_key = st.text_input("SerpApi Key", type="password")
    
    st.divider()
    st.header("🛡️ Seguridad y Horarios")
    limite_diario = st.slider("Límite diario", 10, 150, 50)
    h_inicio = st.time_input("Hora Apertura", value=datetime.strptime("09:00", "%H:%M").time())
    h_fin = st.time_input("Hora Cierre", value=datetime.strptime("19:00", "%H:%M").time())
    
    st.divider()
    st.header("⏰ Piloto Automático")
    target_auto = st.selectbox("Nicho Objetivo", ["Clínica Dental", "Centro Médico", "Kinesiología", "Veterinaria", "Clínica Estetica"])
    zona_auto = st.text_input("Ciudad Objetivo", "Pucón, Chile")
    activar_piloto = st.toggle("Activar Secuencia 3 Días")
    
    st.metric("Enviados Hoy", f"{conteo_hoy} / {limite_diario}")

# --- LÓGICA DE TIEMPO REAL (Lunes a Sábado) ---
ahora = datetime.now()
# 0-5 es Lunes a Sábado. weekday() < 6 cumple la instrucción.
es_dia_permitido = ahora.weekday() < 6 
en_horario = (ahora.time() >= h_inicio and ahora.time() <= h_fin)

if activar_piloto:
    if not es_dia_permitido:
        st.info("😴 Hoy es domingo: El sistema de prospección está en pausa por seguridad.")
    elif not en_horario:
        st.warning(f"🌙 Fuera de horario: Se reactiva a las {h_inicio.strftime('%H:%M')}")
    elif conteo_hoy >= limite_diario:
        st.error("🛑 Límite diario alcanzado.")
    else:
        ejecutar = False
        if st.session_state.ultima_ejecucion is None: ejecutar = True
        else:
            proxima = st.session_state.ultima_ejecucion + timedelta(hours=1)
            if datetime.now() >= proxima: ejecutar = True
            else: st.info(f"⏳ Próximo ciclo en: {str(proxima - datetime.now()).split('.')[0]}")
        
        if ejecutar and evo_url and serp_key:
            ejecutar_workflow_automatico(serp_key, target_auto, zona_auto, evo_url, evo_token, evo_instance, limite_diario)
            st.rerun()

# --- TABS ---
tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🔍 Búsqueda Manual", "📈 Gestión de Secuencias"])

with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leads Totales", len(df_actual))
    c2.metric("Contactados Hoy", conteo_hoy)
    c3.metric("En Día 2", len(df_actual[df_actual["Dia_Secuencia"] == 2]))
    c4.metric("En Día 3", len(df_actual[df_actual["Dia_Secuencia"] == 3]))
    st.dataframe(df_actual.sort_values(by="Fecha_Contacto", ascending=False).head(10), use_container_width=True)

with tab2:
    st.subheader("Búsqueda y Reportes PDF")
    col_m1, col_m2 = st.columns(2)
    target_m = col_m1.selectbox("Especialidad", ["Clínica Dental", "Centro Médico", "Kinesiología", "Clínica Estetica"], key="man_t")
    zona_m = col_m2.text_input("Ciudad", "Pucón, Chile", key="man_z")
    
    if st.button("Escanear y Generar Diagnósticos"):
        if serp_key:
            with st.spinner("Buscando..."):
                params = {"engine": "google_maps", "q": f"{target_m} en {zona_m}", "api_key": serp_key}
                results = requests.get("https://serpapi.com/search", params=params).json().get("local_results", [])
                nuevos = []
                ids_viejos = set(df_actual["Id"].astype(str).tolist())
                for item in results:
                    pid = item.get("place_id")
                    if pid in ids_viejos: continue
                    # Genera el PDF con logo para previsualización o preparación
                    generar_pdf_diagnostico(item.get("title"), item.get("website"))
                    nuevos.append({
                        "Id": pid, "Fecha": datetime.now().strftime("%d/%m/%Y"), "Hora": datetime.now().strftime("%H:%M"),
                        "Evento": item.get("title"), "Ministerio": target_m, "Ubicacion": item.get("address"),
                        "Estado": "Nuevo", "Propuesta": obtener_mensaje_secuencia(item, 1),
                        "Motivo": "Manual", "Telefono": item.get("phone", "No disponible"),
                        "Fecha_Contacto": "Sin contacto", "Reintentos": 0, "Notas": "Reporte PDF Listo", "Dia_Secuencia": 1
                    })
                if nuevos:
                    df_final = pd.concat([df_actual, pd.DataFrame(nuevos)], ignore_index=True)
                    df_final.to_csv(ARCHIVO_LEADS, index=False)
                    st.success(f"Añadidos {len(nuevos)} prospectos.")
                    st.rerun()

    # --- BLOQUE DE PRUEBA REAL (MENSAJE + PDF CON LOGO) ---
    st.divider()
    st.subheader("📲 Prueba de Envío Real (Corporativo)")
    numero_prueba = "+56971394997"
    if st.button(f"Enviar Diagnóstico con Logo a {numero_prueba}"):
        if not (evo_url and evo_token):
            st.error("Configura los datos de Evolution API en el sidebar.")
        else:
            with st.spinner("Enviando pack corporativo..."):
                tel_clean = "56971394997"
                # 1. Enviar el Texto del Día 1
                msg_prueba = obtener_mensaje_secuencia({"title": "Clínica de Prueba"}, 1)
                if enviar_whatsapp_evolution(evo_url, evo_token, evo_instance, tel_clean, msg_prueba):
                    # 2. Generar y Enviar el PDF con el logo incorporado
                    path_prueba = generar_pdf_diagnostico("Clínica de Prueba GestiónVital", False)
                    if enviar_pdf_evolution(evo_url, evo_token, evo_instance, tel_clean, path_prueba):
                        st.success(f"✅ ¡Todo enviado! Revisa el reporte corporativo en {numero_prueba}")
                    else:
                        st.warning("⚠️ Mensaje enviado, pero falló el documento.")
                else:
                    st.error("❌ Falló el envío inicial.")

with tab3:
    st.subheader("Control de Seguimientos")
    df_para_seguimiento = df_actual[(df_actual["Estado"] == "Contactado") & (df_actual["Dia_Secuencia"] < 3)]
    
    if not df_para_seguimiento.empty:
        if st.button("🚀 ENVIAR SIGUIENTE PASO (MASIVO)"):
            if es_dia_permitido:
                with st.spinner("Enviando..."):
                    for idx, row in df_para_seguimiento.iterrows():
                        proximo_dia = row["Dia_Secuencia"] + 1
                        msg = obtener_mensaje_secuencia(row, proximo_dia)
                        tel_clean = "".join(filter(str.isdigit, str(row['Telefono'])))
                        if len(tel_clean) == 9: tel_clean = "56" + tel_clean
                        
                        if enviar_whatsapp_evolution(evo_url, evo_token, evo_instance, tel_clean, msg):
                            df_actual.at[idx, "Dia_Secuencia"] = proximo_dia
                            df_actual.at[idx, "Fecha_Contacto"] = datetime.now().strftime("%d/%m/%Y %H:%M")
                            time.sleep(random.randint(5, 12))
                    df_actual.to_csv(ARCHIVO_LEADS, index=False)
                    st.success("Seguimientos actualizados.")
                    st.rerun()
            else:
                st.error("No se pueden enviar seguimientos los domingos.")

    config_columnas = {
        "Notas": st.column_config.SelectboxColumn(
            "Respuesta",
            options=["Sin notas", "👋 Saludo Automático", "🤖 Bot de Menú", "👤 Humano Interesado", "❌ No Interesado"],
            required=True
        ),
        "Dia_Secuencia": st.column_config.NumberColumn("Día", min_value=1, max_value=3)
    }
    
    edit_df = st.data_editor(df_actual, 
                             column_order=("Evento", "Telefono", "Dia_Secuencia", "Notas", "Estado", "Fecha_Contacto"),
                             column_config=config_columnas,
                             disabled=("Id", "Fecha", "Hora", "Propuesta", "Telefono", "Evento"),
                             use_container_width=True)
    
    if st.button("💾 Sincronizar Cambios de Gestión"):
        edit_df.to_csv(ARCHIVO_LEADS, index=False)
        st.success("Cambios guardados.")