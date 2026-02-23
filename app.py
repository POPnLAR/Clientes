import streamlit as st
import pandas as pd
import os
import time
import requests
import base64
from datetime import datetime

# --- CONFIGURACIÓN ---
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"
COLUMNAS_REQUERIDAS = ["Id", "Fecha", "Hora", "Evento", "Ministerio", "Ubicacion", "Estado", "Telefono", "Fecha_Contacto", "Dia_Secuencia"]
NUMERO_PRUEBA = "56971394997" # Número de prueba configurado

# Variables de entorno para Evolution API (Asegúrate de tenerlas en tu entorno/Secrets)
EVO_URL = os.getenv("EVO_URL", "")
EVO_TOKEN = os.getenv("EVO_TOKEN", "")
EVO_INSTANCE = os.getenv("EVO_INSTANCE", "")

st.set_page_config(page_title="GestiónVital Pro", layout="wide", page_icon="🏥")

# --- DISEÑO MINIMALISTA Y PROFESIONAL ---
st.markdown("""
    <style>
    .stApp { background-color: #F8FAFC !important; }
    [data-testid="stSidebar"] { background-color: #0F172A !important; border-right: 1px solid #E2E8F0; }
    [data-testid="stSidebar"] [data-testid="stMetric"] {
        background-color: #1E293B !important;
        border: 1px solid #334155 !important;
        padding: 15px !important;
        border-radius: 12px !important;
        margin-bottom: 12px !important;
    }
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] div p { color: #94A3B8 !important; font-size: 0.85rem !important; text-transform: uppercase; }
    [data-testid="stSidebar"] [data-testid="stMetricValue"] div { color: #F8FAFC !important; font-size: 1.8rem !important; font-weight: 600 !important; }
    .stTabs [data-baseweb="tab-list"] { background-color: transparent; gap: 15px; }
    .stTabs [data-baseweb="tab"] { color: #64748B !important; font-weight: 500; }
    .stTabs [data-baseweb="tab--active"] { color: #0F172A !important; border-bottom: 2px solid #3B82F6 !important; }
    .stButton>button { background-color: #0F172A !important; color: white !important; border-radius: 8px !important; width: 100%; }
    /* Botón de prueba especial */
    .test-btn > div > button {
        background-color: #3B82F6 !important;
        border: 1px solid #60A5FA !important;
    }
    h1, h2, h3 { color: #0F172A !important; font-weight: 700 !important; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCIONES DE PRUEBA Y COMUNICACIÓN ---
def enviar_mensaje_prueba(numero, dia):
    """Función simplificada para enviar pruebas vía Evolution API"""
    if not EVO_URL or not EVO_TOKEN:
        st.error("Faltan credenciales de Evolution API")
        return False
    
    mensajes = {
        1: "🧪 *PRUEBA DÍA 1:* Hola! Noté una fuga de ingresos en su clínica. Adjunto auditoría...",
        2: "🧪 *PRUEBA DÍA 2:* Sabía que las clínicas reducen el No-Show en un 45%?",
        3: "🧪 *PRUEBA DÍA 3:* Últimos 2 cupos para integración de Ecommerce..."
    }
    
    url = f"{EVO_URL.strip().rstrip('/')}/message/sendText/{EVO_INSTANCE}"
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    payload = {"number": numero, "textMessage": {"text": mensajes[dia]}}
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        return res.status_code in [200, 201]
    except:
        return False

# --- CARGA DE DATOS ---
@st.cache_data(ttl=2)
def cargar_base_segura():
    if os.path.exists(ARCHIVO_LEADS):
        try:
            df = pd.read_csv(ARCHIVO_LEADS)
            for col in COLUMNAS_REQUERIDAS:
                if col not in df.columns:
                    df[col] = 0 if col == "Dia_Secuencia" else "Sin datos"
            return df[COLUMNAS_REQUERIDAS]
        except:
            return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)
    return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)

def guardar_cambios_manuales(df_edit):
    with st.status("Sincronizando...", expanded=False) as status:
        df_edit.to_csv(ARCHIVO_LEADS, index=False)
        time.sleep(1)
        status.update(label="Cambios guardados", state="complete")
    st.toast("Base de datos actualizada")
    time.sleep(0.5)
    st.rerun()

# --- SIDEBAR ---
df_actual = cargar_base_segura()

with st.sidebar:
    st.markdown("<br><h2 style='color: white;'>GestiónVital</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color: #94A3B8;'>Panel de Prospección</p>", unsafe_allow_html=True)
    
    st.metric(label="Total Prospectos", value=len(df_actual))
    st.metric(label="Contactados", value=len(df_actual[df_actual["Estado"] == "Contactado"]))
    st.metric(label="Pendientes", value=len(df_actual[df_actual["Estado"] == "Nuevo"]))
    
    st.markdown("---")
    st.markdown("<p style='color: white; font-size: 0.8rem;'>🛠️ HERRAMIENTAS DE PRUEBA</p>", unsafe_allow_html=True)
    
    # Botón de Pruebas
    st.markdown('<div class="test-btn">', unsafe_allow_html=True)
    if st.button(f"🚀 Enviar Test a {NUMERO_PRUEBA}"):
        with st.spinner("Enviando secuencia de prueba..."):
            success = True
            for d in range(1, 4):
                if not enviar_mensaje_prueba(NUMERO_PRUEBA, d):
                    success = False
                time.sleep(1)
            if success:
                st.success("Secuencia de prueba enviada")
            else:
                st.error("Error en el envío")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("<br>" * 2, unsafe_allow_html=True)
    st.caption(f"v2.8 | {datetime.now().strftime('%d/%m/%Y')}")

# --- CUERPO PRINCIPAL ---
st.title("Prospección Automática")

tabs = st.tabs(["Dashboard", "Editor de Base", "Auditoría"])

with tabs[0]:
    search = st.text_input("Filtrar por clínica...", placeholder="Buscar...", label_visibility="collapsed")
    df_f = df_actual
    if search:
        df_f = df_actual[df_actual['Evento'].str.contains(search, case=False, na=False)]

    st.dataframe(
        df_f.sort_values(by="Id", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Dia_Secuencia": st.column_config.ProgressColumn("Progreso", min_value=0, max_value=3, format="%d/3"),
            "Telefono": "WhatsApp",
            "Evento": "Clínica"
        }
    )

with tabs[1]:
    st.markdown("### 📝 Editor Maestro")
    df_editado = st.data_editor(
        df_actual,
        column_config={
            "Id": st.column_config.NumberColumn("ID", disabled=True),
            "Estado": st.column_config.SelectboxColumn("Estado", options=["Nuevo", "Contactado", "Rechazado", "Cita Agendada"]),
            "Dia_Secuencia": st.column_config.NumberColumn("Día (0-3)", min_value=0, max_value=3),
        },
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="editor_minimal_v8"
    )
    if st.button("GUARDAR CAMBIOS"):
        guardar_cambios_manuales(df_editado)

with tabs[2]:
    if not df_actual.empty:
        clinica = st.selectbox("Seleccionar clínica:", options=df_actual["Evento"].unique())
        detalle = df_actual[df_actual["Evento"] == clinica].iloc[0]
        st.table(detalle)

st.divider()
st.caption("GestionVital Pro - Sistema de automatización clínica.")