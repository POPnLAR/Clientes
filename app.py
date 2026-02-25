import streamlit as st
import pandas as pd
import os
import time
import requests
import unicodedata
import base64
from datetime import datetime

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

# --- DISEÑO CSS DARK PRO ---
st.markdown("""
    <style>
    .stApp { background-color: #0F172A !important; color: #F8FAFC !important; }
    [data-testid="stSidebar"] { background-color: #0F172A !important; border-right: 1px solid #1E293B; }
    [data-testid="stMetric"] { 
        background-color: #1E293B !important; 
        border: 1px solid #334155 !important; 
        padding: 15px !important; 
        border-radius: 12px !important; 
    }
    .stButton>button {
        background-color: #3B82F6 !important;
        color: white !important;
        border-radius: 8px !important;
        width: 100%;
        font-weight: 600 !important;
    }
    /* Color especial para éxito */
    [data-testid="stMetricValue"] { color: #10B981 !important; }
    
    .test-btn > div > button { background-color: #10B981 !important; }
    [data-testid="stDataFrame"] td { color: #F8FAFC !important; }
    </style>
    """, unsafe_allow_html=True)

# Guardar a Git
def push_to_github(filename, content):
    try:
        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{repo}/contents/{filename}"
        
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        res = requests.get(url, headers=headers)
        sha = res.json().get("sha") if res.status_code == 200 else None
        
        data = {
            "message": f"Sincronización desde Panel App {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            "content": base64.b64encode(content.encode('utf-8')).decode('utf-8'),
        }
        if sha:
            data["sha"] = sha
            
        res = requests.put(url, json=data, headers=headers)
        return res.status_code in [200, 201]
    except Exception as e:
        st.error(f"Error de conexión: {str(e)}")
        return False
    
# --- LÓGICA DE PRUEBA ---
def enviar_secuencia_test():
    if not EVO_URL: return
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    base_url = EVO_URL.strip().rstrip('/')
    
    try:
        # Importación dinámica para evitar errores si el archivo no existe aún
        from worker import obtener_mensaje_secuencia
        msg1 = obtener_mensaje_secuencia("Clinica de Prueba", "Santiago", 1)
        requests.post(f"{base_url}/chat/sendPresence/{EVO_INSTANCE}", 
                      json={"number": NUMERO_PRUEBA, "presence": "composing"}, headers=headers)
        time.sleep(2)
        requests.post(f"{base_url}/message/sendText/{EVO_INSTANCE}", 
                      json={"number": NUMERO_PRUEBA, "textMessage": {"text": msg1}}, 
                      headers=headers, timeout=10)
        st.sidebar.success("✅ Test enviado")
    except Exception as e:
        st.sidebar.error(f"❌ Fallo: {str(e)}")

# --- CARGA DE DATOS ---
@st.cache_data(ttl=2)
def cargar_datos():
    if os.path.exists(ARCHIVO_LEADS):
        df = pd.read_csv(ARCHIVO_LEADS)
        for col in COLUMNAS_REQUERIDAS:
            if col not in df.columns: df[col] = 0 if col == "Dia_Secuencia" else ""
        return df
    return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)

# --- APP PRINCIPAL ---
df_actual = cargar_datos()

# Métricas Sidebar Actualizadas
exitos_total = len(df_actual[df_actual["Estado"] == "Agendado"])

with st.sidebar:
    st.markdown("<br><h2 style='color: white;'>GestiónVital</h2>", unsafe_allow_html=True)
    st.metric("Prospectos", len(df_actual))
    st.metric("Casos de Éxito 🏆", exitos_total)
    st.markdown("---")
    if st.button(f"🚀 ENVIAR MSJ PRUEBA"):
        enviar_secuencia_test()
    st.caption(f"v3.5 | {datetime.now().strftime('%d/%m/%Y')}")

st.title("Panel de Prospección Integral")

t1, t2 = st.tabs(["📊 Dashboard", "⚙️ Editor"])

with t1:
    col_a, col_b, col_c, col_d = st.columns(4)
    total = len(df_actual)
    # Consideramos engagement a los contactados + los agendados
    interesados = len(df_actual[df_actual["Estado"].isin(["Contactado", "Agendado"])])
    
    with col_a: st.metric("Cartera Total", total)
    with col_b: st.metric("Casos de Éxito", exitos_total)
    with col_c: st.metric("Conversión", f"{(exitos_total/total*100) if total > 0 else 0:.1f}%")
    with col_d: st.metric("En Secuencia", len(df_actual[df_actual["Dia_Secuencia"] > 0]))

    busqueda = st.text_input("🔍 Buscar Clínica...", placeholder="Ej: Las Condes...", label_visibility="collapsed")
    
    df_f = df_actual.copy()
    if busqueda:
        df_f = df_f[df_f['Evento'].str.contains(busqueda, case=False, na=False) | 
                    df_f['Ubicacion'].str.contains(busqueda, case=False, na=False)]

    df_display = df_f.copy()
    def format_whatsapp_link(tel):
        num = "".join(filter(str.isdigit, str(tel)))
        if not num: return None
        if len(num) == 9: num = "56" + num
        return f"https://wa.me/{num}"

    df_display["WhatsApp"] = df_display["Telefono"].apply(format_whatsapp_link)

    st.dataframe(
        df_display.sort_values(by="Estado", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Id": None,
            "Evento": st.column_config.TextColumn("Clínica / Lead", width="medium"),
            "Ubicacion": "📍 Ubicación",
            "Estado": st.column_config.SelectboxColumn("Estatus", options=["Nuevo", "Contactado", "Agendado", "Finalizado", "Error"]),
            "Dia_Secuencia": st.column_config.ProgressColumn("Madurez", min_value=0, max_value=4, format="%d/4"),
            "WhatsApp": st.column_config.LinkColumn("Chat WhatsApp", display_text="Enviar Mensaje"),
            "Telefono": None,
            "Fecha_Contacto": "Último Contacto",
            "Ministerio": None, "Hora": None, "Fecha": None
        }
    )

with t2:
    st.info("Edición directa de la base de datos con sincronización a GitHub.")
    df_edit = st.data_editor(df_actual, num_rows="dynamic", use_container_width=True, hide_index=True)
    
    if st.button("💾 GUARDAR Y SINCRONIZAR"):
        df_edit.to_csv(ARCHIVO_LEADS, index=False)
        with st.spinner("Sincronizando con GitHub..."):
            csv_content = df_edit.to_csv(index=False)
            if push_to_github(ARCHIVO_LEADS, csv_content):
                st.success("✅ ¡Cambios guardados y sincronizados!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("❌ Error al sincronizar con GitHub.")

st.markdown("---")
st.caption("GestionVital Pro - Sistema de Alta Dirección Clínica.")