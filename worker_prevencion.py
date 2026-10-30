import pandas as pd
import os
import random
import sys
import time
import re
from datetime import datetime, timedelta
import logging

import agent_client
import asignaciones
import captacion
import cobertura
import resumen_email
import serp_client
from evo_client import (
    normalizar_telefono_chile,
    verificar_estado_conexion,
    enviar_mensaje_texto as _evo_enviar_mensaje_texto,
    enviar_alerta_whatsapp,
)

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
NUMERO_OPERADOR = os.getenv("NUMERO_OPERADOR", "")
# Línea de páginas web: negocios de Google Maps SIN sitio web propio, de todo Chile.
# (El nombre "prevencion" en archivos, claves y workflow es histórico: antes era la línea de consultoras
# de prevención de riesgos; se conserva para no perder el CSV, la cobertura ni la configuración del agente.)
ARCHIVO_PREVENCION = "prospeccion_prevencion_pro.csv"
COMUNAS_OBJETIVO = cobertura.COMUNAS_NACIONAL
# Rubros de negocios locales que suelen estar en Google sin web y que se benefician de una página,
# una landing o una tienda online.
TERMINOS_BUSQUEDA = [
    "Peluquería", "Barbería", "Restaurante", "Pastelería", "Taller mecánico", "Ferretería",
    "Veterinaria", "Gimnasio", "Tienda de ropa", "Florería", "Salón de belleza", "Constructora",
]
ARCHIVO_COBERTURA = "cobertura_prevencion.json"
# Cupo mensual de SerpAPI de esta línea: el que dejó libre la línea de almacenes al pausarse (ahora 150 de 250, con clínicas en 100).
ARCHIVO_PRESUPUESTO_SERP = "presupuesto_serpapi_prevencion.json"
LIMITE_MENSUAL_SERPAPI = int(os.getenv("LIMITE_MENSUAL_SERPAPI_PREVENCION", "150"))
# Es B2B: se escribe en horario de oficina, de lunes a viernes (hora de Chile).
HORA_INICIO_ENVIO = int(os.getenv("HORA_INICIO_ENVIO", "9"))
HORA_FIN_ENVIO = int(os.getenv("HORA_FIN_ENVIO", "17"))
# Límite diario de mensajes de ESTA línea (el número de WhatsApp es compartido con las demás líneas).
MAX_MENSAJES_DIARIOS = int(os.getenv("MAX_MENSAJES_DIARIOS_PREVENCION", "20"))
# Reciclaje: cuando no queda a quién escribirle ni leads nuevos que buscar, los que terminaron su
# secuencia sin respuesta vuelven a "Nuevo" pasados RECONTACTO_DIAS. Como cada empresa se contacta pocas
# veces, el plazo es más largo que en las líneas de volumen.
RECONTACTO_DIAS = int(os.getenv("RECONTACTO_DIAS_PREVENCION", "30"))
MAX_RECICLADOS_POR_CICLO = int(os.getenv("MAX_RECICLADOS_POR_CICLO", "5"))

COLUMNAS_MINIMAS = [
    "Id", "Fecha", "Hora", "Evento", "Ministerio", "Ubicacion", "Estado",
    "Telefono", "Dia_Secuencia", "Fecha_Contacto", "Resultado", "Notas", "Version_Mensaje",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# --- UTILIDADES ---
def obtener_ahora_chile():
    """
    Hora actual de Chile como datetime naive (sin tzinfo), comparable con las fechas
    naive que se leen del CSV (Fecha_Contacto). Usa la zona real America/Santiago si
    está disponible y cae a UTC-3 si no.
    """
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        return datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow() - timedelta(hours=3)


def aplicar_spintax(texto):
    def reemplazar(match):
        opciones = match.group(1).split('|')
        return random.choice(opciones)
    return re.sub(r'\{([^{}]*)\}', reemplazar, texto)


def _sin_marcas_de_plantilla(texto):
    """El nombre de la empresa viene de Google Maps: sin llaves ni barras que el spintax tomaría por opciones."""
    return re.sub(r"[{}|]", " ", str(texto or "")).strip()


# --- BÚSQUEDA DE NEGOCIOS SIN WEB EN GOOGLE MAPS ---
def buscar_y_agregar_negocios(df_actual):
    """
    Busca negocios para la siguiente combinación comuna+término del barrido, pidiendo varias
    páginas mientras rindan. Solo agrega negocios SIN sitio web propio y con un celular de Google
    que tenga WhatsApp verificado (sin web no hay dónde rescatar un celular si el de Google es fijo).
    """
    if not SERP_KEY:
        print("❌ SERP_KEY no configurado, no se buscarán nuevos negocios.")
        logging.error("SERP_KEY no configurado; omitiendo búsqueda de negocios sin web.")
        return df_actual

    ahora_cl = obtener_ahora_chile()
    zona, termino = cobertura.siguiente(
        ARCHIVO_COBERTURA, COMUNAS_OBJETIVO, TERMINOS_BUSQUEDA, prioridad=cobertura.PRIORIDAD_NACIONAL
    )
    print(f"🌐 Buscando '{termino}' en: {zona}...")

    tels_en_base = set()
    if not df_actual.empty and "Telefono" in df_actual.columns:
        for t in df_actual["Telefono"]:
            digits = "".join(filter(str.isdigit, str(t)))
            if len(digits) >= 8:
                tels_en_base.add(digits[-9:])
    ultimo_id = int(df_actual["Id"].max()) if not df_actual.empty and "Id" in df_actual.columns else 0
    nuevos_leads = []
    descartes = {"sin_movil": 0, "cadena": 0, "duplicado": 0, "sin_whatsapp": 0, "con_web": 0}

    def procesar_pagina(resultados):
        nonlocal ultimo_id
        candidatos = captacion.preparar_candidatos(
            resultados, tels_en_base, "prevencion", descartes, solo_sin_web=True
        )
        confirmados = captacion.confirmar_con_whatsapp(candidatos, EVO_URL, EVO_TOKEN, EVO_INSTANCE, descartes)
        for cand, tel in confirmados:
            ultimo_id += 1
            nuevos_leads.append({
                "Id": int(ultimo_id),
                "Fecha": ahora_cl.strftime("%d/%m/%Y"),
                "Hora": ahora_cl.strftime("%H:%M"),
                "Evento": cand["place"].get("title", "Negocio"),
                "Ministerio": termino,
                "Ubicacion": zona,
                "Estado": "Nuevo",
                "Telefono": tel,
                "Dia_Secuencia": 0,
                "Fecha_Contacto": "",
                "Resultado": "",
                "Notas": "Sin sitio web propio en Google Maps",
                "Version_Mensaje": "",
            })
        return len(confirmados)

    try:
        estado, paginas = serp_client.buscar_paginas(
            f"{termino} {zona} Chile", SERP_KEY,
            ARCHIVO_PRESUPUESTO_SERP, LIMITE_MENSUAL_SERPAPI, ahora_cl, procesar_pagina,
        )
    except Exception:
        logging.exception("❌ Error en búsqueda de negocios sin web")
        print("❌ Error en búsqueda de negocios (ver logs).")
        return df_actual

    if estado == "presupuesto_agotado":
        return df_actual
    print(f"🧹 Descartados: {descartes} ({paginas} página(s) consultada(s))")
    if estado in ("ok", "sin_resultados"):
        cobertura.marcar_cubierto(ARCHIVO_COBERTURA, zona, termino)
    if estado == "cuota_agotada":
        enviar_alerta_whatsapp(
            EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR,
            "⚠️ Páginas web: SerpAPI dejó de responder en la búsqueda de negocios (posible cuota agotada). Revisar api_key.",
        )
    if nuevos_leads:
        return pd.concat([df_actual, pd.DataFrame(nuevos_leads)], ignore_index=True)
    print("📭 La búsqueda no dejó leads nuevos (duplicados, con web propia, fuera de rubro, sin celular o sin WhatsApp).")
    return df_actual


# --- COMUNICACIONES ---
def enviar_mensaje_texto(numero, mensaje):
    return _evo_enviar_mensaje_texto(EVO_URL, EVO_TOKEN, EVO_INSTANCE, numero, mensaje)


def obtener_mensaje_prevencion(nombre, ubicacion, dia):
    """
    Genera el mensaje para el negocio y una etiqueta de versión para A/B testing.
    Devuelve (mensaje_texto, version). Ofrece una propuesta corta y sin compromiso de página web, landing
    o tienda online; no afirma clientes, cifras ni precios. Sin links: es una conversación entre personas.
    """
    nombre = _sin_marcas_de_plantilla(nombre)
    zona = _sin_marcas_de_plantilla(ubicacion) or "la zona"

    def armar(msg):
        # Los marcadores propios se reemplazan ANTES del spintax: {nombre} sin "|" sería tomado
        # como una opción única y quedaría escrito literalmente.
        return aplicar_spintax(msg.replace("{nombre}", nombre).replace("{zona}", zona))

    if dia == 1:
        # A/B testing sencillo: dos variantes del mensaje inicial
        variante = random.choice(["A", "B"])
        if variante == "A":
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Soy Rodrigo, desarrollo páginas web en Chile. "
                "Vi que *{nombre}* aparece en Google, pero no encontré una página web propia, y hoy muchos "
                "clientes buscan ahí primero antes de llamar o ir al local.\n\n"
                "{¿Les gustaría que les cuente cómo sería una página sencilla para el negocio? Sin compromiso.|"
                "¿Les parece si les envío una propuesta corta de cómo podría verse su página? Sin compromiso.}"
            )
        else:
            msg = (
                "{Hola|Buenas tardes|Hola, ¿qué tal?} 👋 Soy Rodrigo, hago páginas web, landing pages y "
                "tiendas online para negocios de {zona}. Estaba mirando *{nombre}* en Google y vi que "
                "todavía no tienen sitio web propio.\n\n"
                "Si les interesa, respóndanme con un *SI* y les envío una propuesta corta, sin compromiso. 👍"
            )
        return armar(msg), variante

    elif dia == 2:
        msg = (
            "{Hola de nuevo|Hola nuevamente} 👋 Solo para complementar: una página propia (puede ser una "
            "{landing sencilla|página con sus servicios y horarios|tienda online}) hace que los clientes los "
            "encuentren en Google, vean dónde están y qué ofrecen, y les escriban directo por WhatsApp.\n\n"
            "¿Les envío una propuesta corta, sin compromiso?"
        )
        return armar(msg), "D2"

    return "", ""


RESULTADOS_QUE_CIERRAN = {"interesado", "no interesado", "numero equivocado"}


def reciclar_leads_antiguos(df, ahora, excluir=frozenset()):
    """
    Devuelve a "Nuevo" (secuencia desde el día 1) a los negocios que terminaron la secuencia sin
    respuesta (Finalizado) o fallaron (Error) hace al menos RECONTACTO_DIAS. Nunca recicla:
    - "Rechazado" (lo descartó el operador),
    - los ya clasificados (Resultado: interesado / no interesado / número equivocado),
    - los números de `excluir` (respondieron, rechazaron o los atiende el vendedor).
    """
    if df.empty:
        return df, 0

    candidatos = []
    for idx, row in df.iterrows():
        if str(row.get("Estado", "")) not in ["Finalizado", "Error"]:
            continue
        if str(row.get("Resultado", "")).strip().lower() in RESULTADOS_QUE_CIERRAN:
            continue
        fecha_contacto = str(row.get("Fecha_Contacto", "")).strip()
        if not fecha_contacto or fecha_contacto == "nan":
            continue
        if agent_client.clave_telefono(row.get("Telefono", "")) in excluir:
            continue
        try:
            ultima_fecha = datetime.strptime(fecha_contacto, "%d/%m/%Y %H:%M")
        except ValueError:
            continue
        if (ahora - ultima_fecha).days >= RECONTACTO_DIAS:
            candidatos.append(idx)

    if not candidatos:
        return df, 0

    random.shuffle(candidatos)
    reciclados = candidatos[:MAX_RECICLADOS_POR_CICLO]
    for idx in reciclados:
        df.at[idx, "Estado"] = "Nuevo"
        df.at[idx, "Dia_Secuencia"] = 0
        df.at[idx, "Fecha_Contacto"] = ""
    return df, len(reciclados)


def _armar_candidatos(df, ahora, respondieron, estado_resp, asignados=frozenset()):
    """
    Hasta 3 envíos [{'idx','dia'}] para este ciclo. Salta a quienes ya respondieron por
    WhatsApp (los atiende el agente) y a los ya clasificados. Si el agente no respondió
    (estado_resp == "error") solo se envían primeros mensajes, no seguimientos.
    """
    hoy_str = ahora.strftime("%d/%m/%Y")
    candidatos = []
    for idx, row in df.iterrows():
        if hoy_str in str(row.get("Fecha_Contacto", "")):
            continue
        if row["Estado"] in ["Finalizado", "Rechazado", "Error", "Cita Agendada", "Agendado"]:
            continue
        if str(row.get("Resultado", "")).strip().lower() in RESULTADOS_QUE_CIERRAN:
            continue

        if asignaciones.clave_telefono(row.get("Telefono", "")) in asignados:
            continue  # lo atiende el vendedor: no se le escribe desde la secuencia automática

        if agent_client.clave_telefono(row.get("Telefono", "")) in respondieron:
            if not str(row.get("Notas", "")).strip() or str(row.get("Notas")) == "nan":
                df.at[idx, "Notas"] = "Respondió por WhatsApp: secuencia automática pausada"
            continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        if row["Estado"] == "Contactado":
            if estado_resp == "error":
                continue
            try:
                ultima_fecha = datetime.strptime(str(row["Fecha_Contacto"]), "%d/%m/%Y %H:%M")
                if (ahora - ultima_fecha).total_seconds() < 90000:
                    continue
            except Exception:
                logging.warning(
                    "No se pudo parsear Fecha_Contacto para Id %s: %s", row.get("Id"), row.get("Fecha_Contacto")
                )

        if row["Estado"] == "Contactado" and dia_act < 2:  # secuencia corta (2 días)
            candidatos.append({"idx": idx, "dia": dia_act + 1})
        elif row["Estado"] == "Nuevo":
            candidatos.append({"idx": idx, "dia": 1})

    # Límite muy conservador para evitar baneo: solo 3 negocios por ciclo
    random.shuffle(candidatos)
    return candidatos[:3]


# --- CICLO PRINCIPAL ---
def _ciclo(resumen):
    ahora = obtener_ahora_chile()

    # Horario de oficina: lunes a viernes, de HORA_INICIO_ENVIO:00 a HORA_FIN_ENVIO:59 (hora de Chile)
    if ahora.weekday() > 4 or not (HORA_INICIO_ENVIO <= ahora.hour <= HORA_FIN_ENVIO):
        print(f"🕒 Fuera de horario para páginas web (Hora Chile: {ahora.strftime('%A %H:%M')}; "
              f"se opera de lunes a viernes, {HORA_INICIO_ENVIO}:00 a {HORA_FIN_ENVIO}:59).")
        return

    estado_conexion = verificar_estado_conexion(EVO_URL, EVO_INSTANCE, EVO_TOKEN)
    if estado_conexion != "open":
        print(f"🔴 Sesión de WhatsApp no está 'open' (estado: {estado_conexion}). Abortando ciclo sin tocar leads.")
        logging.error("Sesión de WhatsApp caída o desconocida (estado=%s). Deteniendo ciclo.", estado_conexion)
        resumen["alertas"].append(f"Sesión de WhatsApp caída o desconocida (estado={estado_conexion}).")
        sys.exit(1)

    if not os.path.exists(ARCHIVO_PREVENCION):
        df = pd.DataFrame(columns=COLUMNAS_MINIMAS)
    else:
        df = pd.read_csv(ARCHIVO_PREVENCION)

    # Aseguramos que estén todas las columnas requeridas
    for col in COLUMNAS_MINIMAS:
        if col not in df.columns:
            df[col] = 0 if col == "Dia_Secuencia" else ""

    df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
    hoy_str = ahora.strftime("%d/%m/%Y")

    # Control de límite diario de envíos
    enviados_hoy = df["Fecha_Contacto"].astype(str).str.startswith(hoy_str).sum()
    if enviados_hoy >= MAX_MENSAJES_DIARIOS:
        print(f"📵 Límite diario de mensajes alcanzado ({MAX_MENSAJES_DIARIOS}).")
        logging.warning("Límite diario de mensajes alcanzado: %s", MAX_MENSAJES_DIARIOS)
        return

    respondieron, estado_resp = agent_client.obtener_telefonos_que_respondieron()
    asignados = asignaciones.cargar_asignados()
    if respondieron:
        print(f"💬 {len(respondieron)} contactos ya respondieron: su secuencia automática queda pausada.")
    if estado_resp == "error":
        print("⚠️ No se pudo consultar al agente: este ciclo solo se envían primeros mensajes (no seguimientos).")

    candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp, asignados)

    if not candidatos:
        print("📭 Buscando nuevos negocios...")
        antes = len(df)
        df = buscar_y_agregar_negocios(df)
        df.to_csv(ARCHIVO_PREVENCION, index=False)
        despues = len(df)
        print(f"➕ Leads agregados: {max(0, despues-antes)}")
        resumen["nuevos_leads"] = [
            {"Evento": r["Evento"], "Ubicacion": r["Ubicacion"]} for _, r in df.iloc[antes:].iterrows()
        ]

        # Si se agregaron leads, intentamos enviar en el mismo ciclo (para no esperar al próximo cron).
        candidatos = _armar_candidatos(df, ahora, respondieron, estado_resp, asignados)

        if not candidatos:
            print("📭 Aún no hay candidatos después de buscar nuevos negocios.")
            print("♻️ Intentando reciclar leads antiguos...")
            df, total_reciclados = reciclar_leads_antiguos(df, ahora, respondieron | asignados)
            resumen["reciclados"] = total_reciclados
            if total_reciclados > 0:
                print(f"♻️ Leads reciclados para recontacto: {total_reciclados}")
                df.to_csv(ARCHIVO_PREVENCION, index=False)
            else:
                print("📭 Sin leads reciclables.")
            return

    print(f"🚀 Enviando a {len(candidatos)} negocios...")

    for i, item in enumerate(candidatos):
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]
        tel_final = normalizar_telefono_chile(row.get("Telefono", ""))
        if not tel_final or len("".join(filter(str.isdigit, tel_final))) < 8:
            logging.error("Teléfono inválido para Id %s: %s", row.get("Id"), row.get("Telefono"))
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            continue

        msg, version = obtener_mensaje_prevencion(row["Evento"], row["Ubicacion"], dia_obj)
        if not msg:
            continue

        if enviar_mensaje_texto(tel_final, msg):
            agent_client.registrar_mensaje_enviado(tel_final, msg, linea="prevencion")
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 2 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            if dia_obj == 1:
                df.at[idx, "Version_Mensaje"] = version
            print(f"   ✅ Día {dia_obj} enviado a {row['Evento']}.")
            resumen["mensajes"].append({"Evento": row["Evento"], "dia": dia_obj, "ok": True})
        else:
            df.at[idx, "Estado"] = "Error"
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            resumen["mensajes"].append({"Evento": row["Evento"], "dia": dia_obj, "ok": False})

        df.to_csv(ARCHIVO_PREVENCION, index=False)
        if i < len(candidatos) - 1:
            time.sleep(random.randint(300, 600))  # Pausas de 5-10 minutos


def ejecutar_ciclo():
    """Corre el ciclo y, pase lo que pase (incluso si aborta), manda el resumen por correo."""
    resumen = resumen_email.nuevo_resumen()
    try:
        _ciclo(resumen)
    except Exception as e:
        resumen["alertas"].append(f"El ciclo terminó con un error inesperado: {type(e).__name__}: {e}")
        raise
    finally:
        resumen_email.enviar_resumen_si_corresponde(
            resumen, "Páginas web para negocios sin sitio (nacional)", obtener_ahora_chile()
        )


if __name__ == "__main__":
    ejecutar_ciclo()
