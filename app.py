import streamlit as st
import pandas as pd
import os
import json
import time
import requests
import unicodedata
import base64
from datetime import datetime
from urllib.parse import quote

# --- CONFIGURACIÓN ---
MODOS = {
    "🏥 Clínicas Estéticas": "prospeccion_gestionvital_pro.csv",
    "🏪 Almacenes de Barrio": "prospeccion_almacenes_pro.csv",
}
ARCHIVOS_ALERTA = {
    "🏥 Clínicas Estéticas": "alert_status.json",
    "🏪 Almacenes de Barrio": "alert_status_almacenes.json",
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
    "Email",
    "Email_Enviado",
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

# Servicio del agente conversacional (corre aparte, en el VPS). Opcional: si no
# está configurado, la pestaña de Bandeja IA simplemente se muestra deshabilitada.
AGENT_SERVICE_URL = st.secrets.get("AGENT_SERVICE_URL", "")
AGENT_SERVICE_TOKEN = st.secrets.get("AGENT_SERVICE_TOKEN", "")

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

# --- CLIENTE DEL AGENTE CONVERSACIONAL (agent_service.py, corre en el VPS) ---
def _agent_headers():
    return {"x-agent-token": AGENT_SERVICE_TOKEN} if AGENT_SERVICE_TOKEN else {}


def obtener_borradores_pendientes():
    if not AGENT_SERVICE_URL:
        return None
    try:
        res = requests.get(
            f"{AGENT_SERVICE_URL.rstrip('/')}/pending-drafts", headers=_agent_headers(), timeout=10
        )
        if res.status_code == 200:
            return res.json()
        st.error(f"El agente respondió HTTP {res.status_code}: {res.text[:300]}")
        return []
    except Exception as e:
        st.error(f"No se pudo conectar con el servicio del agente: {e}")
        return []


def aprobar_borrador(draft_id, texto_final):
    res = requests.post(
        f"{AGENT_SERVICE_URL.rstrip('/')}/drafts/{draft_id}/approve",
        json={"texto_final": texto_final},
        headers=_agent_headers(),
        timeout=30,
    )
    return res.status_code == 200, res.text


def rechazar_borrador(draft_id):
    res = requests.post(
        f"{AGENT_SERVICE_URL.rstrip('/')}/drafts/{draft_id}/reject",
        headers=_agent_headers(),
        timeout=10,
    )
    return res.status_code == 200, res.text


# --- CONTACTO POR EMAIL ---
# worker.py guarda en "Ministerio" el término de búsqueda que originó el lead
# (ej. "Prospeccion Automatica - Depilacion Laser"). Usamos eso para mandar un
# pitch relevante al tipo de negocio en vez de un texto genérico para todos.
PLANTILLAS_EMAIL = {
    "Clinica Estetica": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Sigo de cerca las clinicas esteticas de {zona} "
            "y me gustaria mostrarles como ayudamos a que el dia a dia sea mas fluido: agenda de "
            "horas, fichas clinicas digitales y control de insumos, todo en un solo lugar.\n\n"
            "Tendrian unos minutos esta semana para conversarlo?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "Centro de Estetica": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Vi el centro de estetica de ustedes en {zona} "
            "y queria contarles como ayudamos a centros como el suyo a organizar mejor la agenda, "
            "fidelizar clientas con recordatorios automaticos y llevar el control de insumos sin planillas.\n\n"
            "Les interesaria una breve llamada esta semana para mostrarles?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "Medicina Estetica": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Trabajo con centros de medicina estetica en {zona} "
            "ayudandolos a digitalizar consentimientos informados, mantener el historial de tratamientos "
            "de cada paciente ordenado y automatizar el seguimiento post-procedimiento.\n\n"
            "Les haria sentido que conversemos 15 minutos esta semana?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "Spa Facial": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Sigo de cerca los spas de {zona} y me llamo la "
            "atencion la propuesta de ustedes. Ayudamos a spas a reducir las inasistencias con "
            "recordatorios automaticos de horas y a fidelizar clientas con seguimiento post-visita.\n\n"
            "Tendrian espacio esta semana para mostrarles como funciona?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "Depilacion Laser": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Trabajo con centros de depilacion laser en {zona}, "
            "ayudandolos a llevar el control de paquetes de sesiones por clienta, avisar automaticamente "
            "cuando toca la proxima sesion y evitar que se pierdan pacientes por falta de seguimiento.\n\n"
            "Les gustaria que conversemos brevemente esta semana?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "Botox y Rellenos": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Vi que en {zona} realizan procedimientos de botox y "
            "rellenos, y queria contarles como ayudamos a centros como el suyo a mantener fichas de "
            "procedimientos ordenadas, consentimientos firmados digitalmente y recordatorios automaticos "
            "para el retoque o control de cada paciente.\n\n"
            "Tendrian unos minutos esta semana para mostrarles como funciona?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
    "_default": {
        "asunto": "GestionVital - Propuesta para {nombre}",
        "cuerpo": (
            "Hola equipo de {nombre},\n\n"
            "Mi nombre es Rodrigo, de GestionVital. Sigo de cerca los negocios de {zona} "
            "y me gustaria mostrarles como ayudamos a optimizar el dia a dia: agenda, "
            "fichas de pacientes/clientes y control de insumos, todo en un solo lugar.\n\n"
            "Tendrian unos minutos esta semana para conversarlo?\n\n"
            "Saludos,\nRodrigo\nGestionVital\nhttps://gestionvitalpro.cl"
        ),
    },
}


def _detectar_categoria(ministerio, plantillas):
    """
    Detecta a qué término de búsqueda (Clínica Estética, Spa Facial, etc.)
    corresponde un lead a partir de la columna Ministerio, para elegir la
    plantilla de contacto (email o WhatsApp) más relevante. `plantillas` es
    el diccionario de plantillas (sus claves son los términos posibles).
    """
    if isinstance(ministerio, str):
        texto = limpiar_acentos(ministerio).lower()
        for clave in plantillas:
            if clave == "_default":
                continue
            if limpiar_acentos(clave).lower() in texto:
                return clave
    return "_default"


def generar_mailto(email, nombre, ubicacion, ministerio=""):
    """
    Genera un link mailto: con asunto y cuerpo precargados, usando la plantilla
    que corresponde al término de búsqueda que originó el lead (columna
    Ministerio). Abre el cliente de correo del usuario con un borrador editable
    — no envía nada automáticamente, el operador revisa y presiona enviar él mismo.
    """
    if not isinstance(email, str) or "@" not in email:
        return None
    nombre_limpio = limpiar_acentos(nombre) if nombre else "equipo"
    zona = ubicacion if isinstance(ubicacion, str) and ubicacion.strip() else "su zona"
    plantilla = PLANTILLAS_EMAIL[_detectar_categoria(ministerio, PLANTILLAS_EMAIL)]
    asunto = plantilla["asunto"].format(nombre=nombre_limpio)
    cuerpo = plantilla["cuerpo"].format(nombre=nombre_limpio, zona=zona)
    return f"mailto:{email.strip()}?subject={quote(asunto)}&body={quote(cuerpo)}"


# --- CONTACTO POR WHATSAPP ---
# Mensajes de primer contacto: cortos, sin sonar a spam, y cerrando con una
# pregunta sobre un dolor concreto del rubro (no ofrecen la presentación de
# entrada, buscan que respondan y abrir la conversación desde ahí). Sin
# emojis: un emoji rompió el encoding en wa.me en algunos entornos.
PLANTILLAS_WHATSAPP = {
    "Clinica Estetica": (
        "Hola, soy Rodrigo de GestionVital. Vi la clinica *{nombre}* en "
        "{zona} y trabajo ayudando a clinicas esteticas a ordenar la "
        "agenda, las fichas clinicas y el control de insumos en un solo "
        "lugar. Como estan agendando las horas hoy en dia?"
    ),
    "Centro de Estetica": (
        "Hola, soy Rodrigo de GestionVital. Vi el centro de estetica "
        "*{nombre}* en {zona} y trabajo ayudando a centros como el suyo a "
        "organizar la agenda y fidelizar clientas con recordatorios "
        "automaticos. Que estan usando hoy para agendar las horas?"
    ),
    "Medicina Estetica": (
        "Hola, soy Rodrigo de GestionVital. Vi a *{nombre}* en {zona} y "
        "trabajo con centros de medicina estetica ayudandolos a digitalizar "
        "consentimientos informados y el historial de tratamientos de cada "
        "paciente. Como estan llevando hoy ese historial?"
    ),
    "Spa Facial": (
        "Hola, soy Rodrigo de GestionVital. Vi el spa *{nombre}* en {zona} "
        "y trabajo ayudando a spas a reducir las inasistencias con "
        "recordatorios automaticos de horas. Como estan agendando y "
        "confirmando las horas hoy?"
    ),
    "Depilacion Laser": (
        "Hola, soy Rodrigo de GestionVital. Vi a *{nombre}* en {zona} y "
        "trabajo con centros de depilacion laser ayudandolos a controlar "
        "los paquetes de sesiones por clienta y avisar automaticamente "
        "cuando toca la proxima. Como llevan hoy el control de las sesiones "
        "de cada clienta?"
    ),
    "Botox y Rellenos": (
        "Hola, soy Rodrigo de GestionVital. Vi que *{nombre}* en {zona} "
        "realiza botox y rellenos, y trabajo ayudando a centros como el "
        "suyo a mantener las fichas de procedimientos y los consentimientos "
        "ordenados. Como estan llevando hoy esas fichas?"
    ),
    "_default": (
        "Hola, soy Rodrigo de GestionVital. Vi el negocio de *{nombre}* en "
        "{zona} y trabajo ayudando a negocios como el suyo a optimizar la "
        "agenda, las fichas y el control de insumos. Que estan usando hoy "
        "para agendar?"
    ),
}


def format_whatsapp_link(tel, nombre="", ubicacion="", ministerio=""):
    """
    Genera el link de wa.me con un mensaje inicial precargado (editable por
    el operador antes de enviar), usando la plantilla del término que
    originó el lead. Sin nombre/ubicación disponibles, igual arma el link
    pero con un mensaje genérico.
    """
    tel_norm = normalizar_telefono_chile(tel)
    if not tel_norm:
        return None
    num = "".join(filter(str.isdigit, str(tel_norm)))
    if not num:
        return None
    nombre_limpio = limpiar_acentos(nombre) if isinstance(nombre, str) and nombre.strip() else "su negocio"
    zona = ubicacion if isinstance(ubicacion, str) and ubicacion.strip() else "su zona"
    plantilla = PLANTILLAS_WHATSAPP[_detectar_categoria(ministerio, PLANTILLAS_WHATSAPP)]
    mensaje = plantilla.format(nombre=nombre_limpio, zona=zona)
    return f"https://wa.me/{num}?text={quote(mensaje)}"


COLUMNAS_TEXTO = [c for c in COLUMNAS_REQUERIDAS if c != "Dia_Secuencia"]


# --- CARGA DE DATOS ---
@st.cache_data(ttl=2)
def cargar_datos(archivo):
    if os.path.exists(archivo):
        df = pd.read_csv(archivo)
        for col in COLUMNAS_REQUERIDAS:
            if col not in df.columns: df[col] = 0 if col == "Dia_Secuencia" else ""
        # Columnas de texto con celdas vacías se leen como float (NaN) en vez de
        # string, lo que rompe st.data_editor con TextColumn ("incompatible type").
        for col in COLUMNAS_TEXTO:
            df[col] = df[col].fillna("").astype(str)
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

archivo_alerta_actual = ARCHIVOS_ALERTA.get(unidad)
if archivo_alerta_actual and os.path.exists(archivo_alerta_actual):
    try:
        with open(archivo_alerta_actual, "r", encoding="utf-8") as f:
            alerta = json.load(f)
        st.error(
            f"⚠️ Sistema detenido desde {alerta.get('timestamp', '?')} — "
            f"{alerta.get('motivo', 'motivo desconocido')}. {alerta.get('detalle', '')}"
        )
    except Exception:
        st.error(f"⚠️ Hay una alerta activa en {archivo_alerta_actual} pero no se pudo leer.")

st.title(f"Panel: {unidad}")

# La pestaña de IA solo se muestra si el servicio está configurado (AGENT_SERVICE_URL);
# mientras no esté desplegado, ocultarla evita clutter de una pestaña sin uso real.
etiquetas_tabs = ["📊 Dashboard Real-Time", "⚙️ Editor de Base"]
if AGENT_SERVICE_URL:
    etiquetas_tabs.append("💬 Bandeja de Respuestas IA")
etiquetas_tabs.append("📧 Contactar por Email")

tabs = st.tabs(etiquetas_tabs)
t1, t2 = tabs[0], tabs[1]
t3 = tabs[2] if AGENT_SERVICE_URL else None
t4 = tabs[-1]

with t1:
    con_email = df_actual["Email"].astype(str).str.contains("@", na=False)
    col_a, col_b, col_c, col_d, col_e = st.columns(5)

    with col_a: st.metric("Cartera", total_leads)
    with col_b: st.metric("Agendados", exitos)
    with col_c: st.metric("Conversión", f"{(exitos/total_leads*100) if total_leads > 0 else 0:.1f}%")
    with col_d: st.metric("En Seguimiento", len(df_actual[df_actual["Dia_Secuencia"] > 0]))
    with col_e: st.metric("📧 Con Email", int(con_email.sum()))

    col_busqueda, col_filtro = st.columns([3, 1])
    with col_busqueda:
        busqueda = st.text_input("🔍 Filtrar por nombre o ubicación...", placeholder="Ej: San Miguel...", label_visibility="collapsed")
    with col_filtro:
        solo_con_email = st.checkbox("Solo con email")

    st.caption(
        "⚠️ El botón WhatsApp \"Chat Directo\" es para casos puntuales/emergencias — el "
        "sistema automático ya gestiona el contacto normal de cada lead."
    )

    df_f = df_actual.copy()
    if busqueda:
        df_f = df_f[df_f['Evento'].str.contains(busqueda, case=False, na=False) |
                    df_f['Ubicacion'].str.contains(busqueda, case=False, na=False)]
    if solo_con_email:
        df_f = df_f[df_f["Email"].astype(str).str.contains("@", na=False)]

    df_display = df_f.copy()
    df_display["WhatsApp"] = df_display.apply(
        lambda r: format_whatsapp_link(r["Telefono"], r["Evento"], r["Ubicacion"], r["Ministerio"]), axis=1
    )

    # Configuración dinámica de progreso según la línea
    max_secuencia = 2 if "Almacenes" in unidad else 4

    # Prioriza en la vista a quienes tienen email y aún no fueron contactados por ese canal.
    df_display["_prioridad_email"] = (
        df_display["Email"].astype(str).str.contains("@", na=False)
        & (df_display["Email_Enviado"] != "Si")
    )
    df_display = df_display.sort_values(
        by=["_prioridad_email", "Estado"], ascending=[False, False]
    ).drop(columns="_prioridad_email")

    st.dataframe(
        df_display,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "Evento", "Ubicacion", "Estado", "Email", "Email_Enviado", "WhatsApp",
            "Dia_Secuencia", "Resultado", "Notas", "Fecha_Contacto",
        ],
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
            "Email": st.column_config.TextColumn("📧 Email", width="medium"),
            "Email_Enviado": st.column_config.SelectboxColumn("Email Enviado", options=["No", "Si"]),
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

if t3 is not None:
    with t3:
        st.warning(
            "Estos borradores fueron redactados por IA a partir de respuestas reales de WhatsApp. "
            "Revísalos y edítalos antes de aprobar — nada se envía sin tu aprobación."
        )
        if st.button("🔄 Actualizar bandeja"):
            st.rerun()

        borradores = obtener_borradores_pendientes()
        if borradores is None:
            pass
        elif not borradores:
            st.success("📭 No hay borradores pendientes de revisión.")
        else:
            for b in borradores:
                lead = b.get("lead") or {}
                nombre_lead = lead.get("Evento") or b["telefono_normalizado"]
                with st.container(border=True):
                    st.markdown(f"**{nombre_lead}** — {b['telefono_normalizado']}")
                    if lead.get("Ubicacion"):
                        st.caption(f"📍 {lead['Ubicacion']} · Estado actual: {lead.get('Estado', '?')}")
                    st.markdown(f"> 💬 *Mensaje del prospecto:* {b.get('mensaje_entrante', '(sin texto)')}")

                    texto_editado = st.text_area(
                        "Borrador de respuesta (editable):",
                        value=b["texto_borrador"],
                        key=f"draft_{b['id']}",
                    )

                    col_ok, col_no = st.columns(2)
                    with col_ok:
                        if st.button("✅ Aprobar y enviar", key=f"approve_{b['id']}"):
                            ok, detalle = aprobar_borrador(b["id"], texto_editado)
                            if ok:
                                st.success("Enviado.")
                                st.rerun()
                            else:
                                st.error(f"No se pudo enviar: {detalle}")
                    with col_no:
                        if st.button("🚫 Rechazar", key=f"reject_{b['id']}"):
                            ok, detalle = rechazar_borrador(b["id"])
                            if ok:
                                st.info("Borrador rechazado.")
                                st.rerun()
                            else:
                                st.error(f"No se pudo rechazar: {detalle}")

with t4:
    st.warning(
        "El botón '✉️ Redactar' abre tu cliente de correo (Gmail, Outlook, etc.) con un "
        "borrador precargado. Nada se envía automáticamente: tú revisas y presionas enviar."
    )
    st.caption(
        "⚠️ Uso solo para casos puntuales/emergencias. El sistema automático (worker.py) ya "
        "gestiona el contacto y el seguimiento normal de cada lead — usar este botón de forma "
        "rutinaria puede duplicar mensajes a la misma persona."
    )

    df_email = df_actual[df_actual["Email"].astype(str).str.contains("@", na=False)].copy()

    total_con_email = len(df_email)
    enviados = int((df_email["Email_Enviado"] == "Si").sum()) if total_con_email else 0
    pendientes = total_con_email - enviados

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1: st.metric("Con email", total_con_email)
    with col_m2: st.metric("Contactados", enviados)
    with col_m3: st.metric("Pendientes", pendientes)

    if total_con_email == 0:
        st.info("Todavía no hay prospectos con email capturado en esta línea de negocio.")
    else:
        solo_pendientes = st.checkbox("Mostrar solo pendientes de contactar", value=True)
        if solo_pendientes:
            df_email = df_email[df_email["Email_Enviado"] != "Si"]

        if df_email.empty:
            st.success("📭 No quedan prospectos con email pendientes de contactar.")
        else:
            df_email["Redactar"] = df_email.apply(
                lambda r: generar_mailto(r["Email"], r["Evento"], r["Ubicacion"], r["Ministerio"]), axis=1
            )
            df_email = df_email.sort_values(by="Ubicacion")

            columnas_email = ["Evento", "Ubicacion", "Email", "Redactar", "Email_Enviado", "Notas"]
            edited = st.data_editor(
                df_email[columnas_email],
                use_container_width=True,
                hide_index=True,
                disabled=["Evento", "Ubicacion", "Email", "Redactar"],
                key=f"editor_email_{unidad}",
                column_config={
                    "Evento": st.column_config.TextColumn("Nombre Comercial", width="medium"),
                    "Ubicacion": "📍 Comuna",
                    "Email": st.column_config.TextColumn("Email", width="medium"),
                    "Redactar": st.column_config.LinkColumn("✉️ Acción", display_text="Redactar"),
                    "Email_Enviado": st.column_config.SelectboxColumn("¿Contactado?", options=["No", "Si"]),
                    "Notas": st.column_config.TextColumn("Notas de seguimiento", width="large"),
                },
            )

            if st.button("💾 Guardar seguimiento de email"):
                df_guardar = df_actual.copy()
                df_guardar.loc[edited.index, "Email_Enviado"] = edited["Email_Enviado"]
                df_guardar.loc[edited.index, "Notas"] = edited["Notas"]
                df_guardar.to_csv(archivo_actual, index=False)
                with st.spinner("Sincronizando con GitHub..."):
                    if push_to_github(archivo_actual, df_guardar.to_csv(index=False)):
                        st.success("✅ Seguimiento guardado.")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error("❌ Falló la sincronización con GitHub.")

st.markdown("---")
st.caption("ServiGod Pro System - Inteligencia de Negocios Chilena.")