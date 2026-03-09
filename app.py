import streamlit as st
import pandas as pd
import os
import time
import requests
import unicodedata
import base64
from datetime import datetime

# --- CONFIGURACIÓN ---
MODOS = {
    "🏥 Clínicas Estéticas": "prospeccion_gestionvital_pro.csv",
    "🏪 Almacenes de Barrio": "prospeccion_almacenes_pro.csv",
}
COLUMNAS_REQUERIDAS = [
    "Id",
    "Fecha",
    "Hora",
    "Evento",
    "Ministerio",
    "Ubicacion",
    "Estado",
    "Telefono",
    "Fecha_Contacto",
    "Dia_Secuencia",
    "Resultado",
    "Notas",
    "Version_Mensaje",
]
NUMERO_PRUEBA = "56971394997"

# Conexión Segura a Secrets
try:
    EVO_URL = st.secrets["EVO_URL"]
    EVO_TOKEN = st.secrets["EVO_TOKEN"]
    EVO_INSTANCE = st.secrets["EVO_INSTANCE"]
except Exception:
    st.error("⚠️ Error: No se encontraron las credenciales en Secrets.")
    EVO_URL = EVO_TOKEN = EVO_INSTANCE = None

st.set_page_config(page_title="GestiónVital Pro Multi-SaaS", layout="wide", page_icon="📈")

# --- UTILIDADES ---
def limpiar_acentos(text):
    if not isinstance(text, str):
        return str(text)
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def normalizar_telefono_chile(raw):
    """
    Normaliza distintos formatos de teléfono chileno a un formato consistente.
    Preferimos devolver '56XXXXXXXXX' cuando es posible.
    """
    digits = "".join(filter(str.isdigit, str(raw)))
    if not digits:
        return ""

    # Ya viene con código de país
    if digits.startswith("56") and len(digits) >= 11:
        return digits

    # Quitar ceros iniciales típicos (09..., 02..., etc.)
    while digits.startswith("0"):
        digits = digits[1:]

    # Celular típico 9XXXXXXXX
    if len(digits) == 9 and digits.startswith("9"):
        return "56" + digits

    # Fijo típico 2XXXXXXX u otros códigos de área de 1 dígito + 7
    if len(digits) == 9 and not digits.startswith("9"):
        return "56" + digits

    # Si hay más de 9 dígitos, intenta con los últimos 9
    if len(digits) > 9:
        ultimos = digits[-9:]
        if len(ultimos) == 9:
            return "56" + ultimos

    return digits

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
    [data-testid="stMetricValue"] { color: #10B981 !important; }
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
            "message": f"Sincronización {filename} {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            "content": base64.b64encode(content.encode('utf-8')).decode('utf-8'),
        }
        if sha: data["sha"] = sha
        res = requests.put(url, json=data, headers=headers)
        return res.status_code in [200, 201]
    except Exception as e:
        st.error(f"Error de conexión: {str(e)}")
        return False

# --- CARGA DE DATOS ---
@st.cache_data(ttl=2)
def cargar_datos(archivo):
    if os.path.exists(archivo):
        df = pd.read_csv(archivo)
        for col in COLUMNAS_REQUERIDAS:
            if col not in df.columns: df[col] = 0 if col == "Dia_Secuencia" else ""
        return df
    return pd.DataFrame(columns=COLUMNAS_REQUERIDAS)

# --- SIDEBAR & NAVEGACIÓN ---
with st.sidebar:
    st.markdown("<br><h2 style='color: white;'>ServiGod Control</h2>", unsafe_allow_html=True)
    unidad = st.selectbox("🎯 Unidad de Negocio", list(MODOS.keys()))
    archivo_actual = MODOS[unidad]
    
    df_actual = cargar_datos(archivo_actual)
    
    total_leads = len(df_actual)
    exitos = len(df_actual[df_actual["Estado"] == "Agendado"])
    
    st.metric("Prospectos Totales", total_leads)
    st.metric("Casos de Éxito 🏆", exitos)
    
    st.markdown("---")
    if st.button(f"🚀 ENVIAR TEST ({unidad.split()[1]})"):
        st.info("Función de test habilitada para la instancia actual.")
    st.caption(f"v4.0 | Multi-Línea")

st.title(f"Panel: {unidad}")

t1, t2 = st.tabs(["📊 Dashboard Real-Time", "⚙️ Editor de Base"])

with t1:
    col_a, col_b, col_c, col_d = st.columns(4)
    
    with col_a: st.metric("Cartera", total_leads)
    with col_b: st.metric("Agendados", exitos)
    with col_c: st.metric("Conversión", f"{(exitos/total_leads*100) if total_leads > 0 else 0:.1f}%")
    with col_d: st.metric("En Seguimiento", len(df_actual[df_actual["Dia_Secuencia"] > 0]))

    busqueda = st.text_input("🔍 Filtrar por nombre o ubicación...", placeholder="Ej: San Miguel...", label_visibility="collapsed")
    
    df_f = df_actual.copy()
    if busqueda:
        df_f = df_f[df_f['Evento'].str.contains(busqueda, case=False, na=False) | 
                    df_f['Ubicacion'].str.contains(busqueda, case=False, na=False)]

    # Formatear link de WhatsApp
    def format_whatsapp_link(tel):
        tel_norm = normalizar_telefono_chile(tel)
        if not tel_norm:
            return None
        num = "".join(filter(str.isdigit, str(tel_norm)))
        if not num:
            return None
        return f"https://wa.me/{num}"

    df_display = df_f.copy()
    df_display["WhatsApp"] = df_display["Telefono"].apply(format_whatsapp_link)

    # Configuración dinámica de progreso según la línea
    max_secuencia = 2 if "Almacenes" in unidad else 4

    st.dataframe(
        df_display.sort_values(by="Estado", ascending=False),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Id": None,
            "Evento": st.column_config.TextColumn("Nombre Comercial", width="medium"),
            "Ubicacion": "📍 Comuna",
            "Estado": st.column_config.SelectboxColumn("Estatus", options=["Nuevo", "Contactado", "Agendado", "Finalizado", "Error"]),
            "Dia_Secuencia": st.column_config.ProgressColumn("Madurez", min_value=0, max_value=max_secuencia, format="%d pasos"),
            "WhatsApp": st.column_config.LinkColumn("WhatsApp", display_text="Chat Directo"),
            "Resultado": st.column_config.SelectboxColumn(
                "Resultado",
                options=["", "Interesado", "No interesado", "Numero equivocado"],
            ),
            "Notas": st.column_config.TextColumn("Notas", width="large"),
            "Version_Mensaje": "Versión Msg",
            "Telefono": None,
            "Fecha_Contacto": "Último Contacto",
            "Ministerio": None,
            "Hora": None,
            "Fecha": None,
        }
    )

with t2:
    st.warning(f"⚠️ Estás editando el archivo: {archivo_actual}")
    df_edit = st.data_editor(df_actual, num_rows="dynamic", use_container_width=True, hide_index=True)
    
    if st.button("💾 GUARDAR CAMBIOS Y SUBIR A GITHUB"):
        df_edit.to_csv(archivo_actual, index=False)
        with st.spinner("Actualizando repositorio..."):
            csv_content = df_edit.to_csv(index=False)
            if push_to_github(archivo_actual, csv_content):
                st.success(f"✅ ¡{archivo_actual} actualizado con éxito!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("❌ Falló la sincronización con GitHub.")

st.markdown("---")
st.caption("ServiGod Pro System - Inteligencia de Negocios Chilena.")