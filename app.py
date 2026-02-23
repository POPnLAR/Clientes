import streamlit as st
import pandas as pd
import os
import time
import requests
import base64
import unicodedata
from datetime import datetime
from fpdf import FPDF

# --- CONFIGURACIÓN ---
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"
COLUMNAS_REQUERIDAS = ["Id", "Fecha", "Hora", "Evento", "Ministerio", "Ubicacion", "Estado", "Telefono", "Fecha_Contacto", "Dia_Secuencia"]
NUMERO_PRUEBA = "56971394997"

# Conexión Segura a Secrets
try:
    EVO_URL = st.secrets["EVO_URL"]
    EVO_TOKEN = st.secrets["EVO_TOKEN"]
    EVO_INSTANCE = st.secrets["EVO_INSTANCE"]
except Exception:
    st.error("⚠️ Error: No se encontraron las credenciales en Secrets.")
    EVO_URL = EVO_TOKEN = EVO_INSTANCE = None

st.set_page_config(page_title="GestiónVital Pro", layout="wide", page_icon="🏥")

# --- UTILIDADES ---
def limpiar_acentos(text):
    if not isinstance(text, str): return str(text)
    return "".join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

# --- DISEÑO CSS MINIMALISTA ---
st.markdown("""
    <style>
    .stApp { background-color: #F8FAFC !important; }
    [data-testid="stSidebar"] { background-color: #0F172A !important; border-right: 1px solid #E2E8F0; }
    [data-testid="stSidebar"] [data-testid="stMetric"] {
        background-color: #1E293B !important;
        border: 1px solid #334155 !important;
        padding: 15px !important;
        border-radius: 12px !important;
    }
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] div p { color: #94A3B8 !important; font-size: 0.8rem !important; text-transform: uppercase; }
    [data-testid="stSidebar"] [data-testid="stMetricValue"] div { color: #F8FAFC !important; font-size: 1.6rem !important; }
    
    /* Botón de Prueba Azul Vibrante */
    .test-btn > div > button {
        background-color: #3B82F6 !important;
        color: white !important;
        font-weight: bold !important;
        border: none !important;
        height: 45px !important;
        border-radius: 10px !important;
    }
    
    .stTabs [data-baseweb="tab-list"] { background-color: transparent; gap: 15px; }
    .stTabs [data-baseweb="tab--active"] { border-bottom: 2px solid #3B82F6 !important; }
    h1, h2, h3 { color: #0F172A !important; }
    </style>
    """, unsafe_allow_html=True)

# --- GENERADOR DE PDF MODERNO ---
def generar_pdf_auditoria(nombre_clinica):
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_fill_color(15, 23, 42)
        pdf.rect(0, 0, 210, 45, 'F')
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", 'B', 20)
        pdf.set_xy(10, 15)
        pdf.cell(0, 10, "AUDITORÍA DE EFICIENCIA DIGITAL", ln=True)
        pdf.set_font("Arial", '', 12)
        pdf.cell(0, 10, f"Para: {limpiar_acentos(nombre_clinica).upper()}", ln=True)
        
        pdf.set_y(55)
        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Arial", 'B', 14)
        pdf.cell(0, 10, "DIAGNÓSTICO DE PÉRDIDAS ESTIMADAS", ln=True)
        
        puntos = [
            ("No-Show", "30% de inasistencia por falta de recordatorios."),
            ("Gestión", "2 horas/día perdidas en confirmación manual."),
            ("Digital", "Riesgo legal por falta de firma electrónica.")
        ]
        for tit, desc in puntos:
            pdf.set_font("Arial", 'B', 11); pdf.set_text_color(59, 130, 246)
            pdf.cell(0, 8, f"> {tit}", ln=True)
            pdf.set_font("Arial", '', 10); pdf.set_text_color(71, 85, 105)
            pdf.multi_cell(0, 5, desc); pdf.ln(2)
            
        path = "auditoria_test.pdf"
        pdf.output(path)
        return path
    except: return None

# --- LÓGICA DE PRUEBA ---
def enviar_secuencia_test():
    if not EVO_URL: return
    
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    base_url = EVO_URL.strip().rstrip('/')
    
    # DÍA 1 + PDF
    path_pdf = generar_pdf_auditoria("Clínica de Prueba")
    msg1 = "🧪 *TEST DÍA 1:* Hola! Noté una fuga de ingresos en su clínica. Adjunto auditoría..."
    requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", json={"number": NUMERO_PRUEBA, "textMessage": {"text": msg1}}, headers=headers)
    
    if path_pdf and os.path.exists(path_pdf):
        with open(path_pdf, "rb") as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        requests.post(f"{base_url}/message/sendMedia/{EVO_INSTANCE}", 
                      json={"number": NUMERO_PRUEBA, "mediaMessage": {"mediatype": "document", "fileName": "Auditoria.pdf", "media": b64}}, headers=headers)
        os.remove(path_pdf)
    
    # DÍA 2
    time.sleep(2)
    msg2 = "🧪 *TEST DÍA 2:* Sabía que las clínicas automatizadas reducen el No-Show un 45%?"
    requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", json={"number": NUMERO_PRUEBA, "textMessage": {"text": msg2}}, headers=headers)
    
    st.sidebar.success("✅ Test enviado a WhatsApp")

# --- CARGA DE DATOS ---
@st.cache_data(ttl=2)
def cargar_datos():
    if os.path.exists(ARCHIVO_LEADS):
        return pd.read_csv(ARCHIVO_LEADS)
    return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)

# --- SIDEBAR ---
df_actual = cargar_datos()

with st.sidebar:
    st.markdown("<br><h2 style='color: white;'>GestiónVital</h2>", unsafe_allow_html=True)
    st.metric("Prospectos", len(df_actual))
    st.metric("Contactados", len(df_actual[df_actual["Estado"] == "Contactado"]))
    
    st.markdown("---")
    st.markdown("<p style='color: white; font-size: 0.8rem;'>LABORATORIO DE PRUEBAS</p>", unsafe_allow_html=True)
    st.markdown('<div class="test-btn">', unsafe_allow_html=True)
    if st.button(f"🚀 ENVIAR TEST A {NUMERO_PRUEBA[-4:]}"):
        enviar_secuencia_test()
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption(f"v2.9 | {datetime.now().strftime('%d/%m/%Y')}")

# --- CUERPO ---
st.title("Panel de Prospección")
t1, t2 = st.tabs(["Dashboard", "Editor"])

with t1:
    st.dataframe(df_actual, use_container_width=True, hide_index=True)

with t2:
    df_edit = st.data_editor(df_actual, num_rows="dynamic", use_container_width=True, hide_index=True)
    if st.button("GUARDAR"):
        df_edit.to_csv(ARCHIVO_LEADS, index=False)
        st.rerun()