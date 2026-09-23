"""
Servicio siempre-activo (FastAPI) que:
  1. Recibe el webhook de mensajes entrantes de Evolution API.
  2. Genera un borrador de respuesta con Gemini.
  3. Expone endpoints internos para que el dashboard de Streamlit (app.py)
     muestre la cola de borradores y permita aprobar/rechazar/enviar.

Diseñado para desplegarse en el mismo VPS donde ya corre Evolution API
(despliegue real fuera del alcance de este código: systemd/Docker, TLS, etc.
lo hace el operador). Corre con: uvicorn agent_service:app --host 0.0.0.0 --port 8001

IMPORTANTE: el shape exacto del payload del webhook de Evolution API no está
verificado contra la instancia real (ver plan). El parser de abajo es
tolerante e intenta varias rutas conocidas de distintas versiones de
Evolution API; cualquier payload no reconocido se loguea completo para poder
ajustar el parser rápido.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

import calendar_client
import conversation_store as store
import filtro_mensajes
from evo_client import (
    enviar_alerta_whatsapp,
    enviar_mensaje_texto as _evo_enviar_mensaje_texto,
    normalizar_telefono_chile,
    verificar_estado_conexion,
)
from gemini_client import _borrador_fallback, generar_borrador

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
AGENT_SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN")
NUMERO_OPERADOR = os.getenv("NUMERO_OPERADOR", "")
CHEQUEO_SALUD_INTERVALO_SEG = int(os.getenv("CHEQUEO_SALUD_INTERVALO_SEG", str(20 * 60)))
ARCHIVO_ALERTA_AGENTE = "alert_status_agente.json"

CSVS_POR_LINEA = {
    "clinicas": "prospeccion_gestionvital_pro.csv",
    "almacenes": "prospeccion_almacenes_pro.csv",
}

app = FastAPI(title="GestiónVital Pro - Agente Conversacional")


async def _chequeo_salud_periodico():
    """
    Chequea el estado de la sesión de WhatsApp cada CHEQUEO_SALUD_INTERVALO_SEG
    (por defecto 20 min), independiente del cron de los workers. Complementa la
    Fase 1: este es el chequeo más frecuente, así que suele detectar una caída
    antes que el próximo ciclo del worker.
    """
    while True:
        try:
            estado = verificar_estado_conexion(EVO_URL, EVO_INSTANCE, EVO_TOKEN)
            if estado != "open":
                motivo = f"Chequeo periódico del agente: estado de sesión = '{estado}'."
                logging.error(motivo)
                with open(ARCHIVO_ALERTA_AGENTE, "w", encoding="utf-8") as f:
                    json.dump(
                        {"motivo": "sesion_whatsapp_caida", "detalle": motivo,
                         "timestamp": datetime.utcnow().isoformat()},
                        f, ensure_ascii=False, indent=2,
                    )
                enviar_alerta_whatsapp(EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR, f"⚠️ GestiónVital: {motivo}")
            elif os.path.exists(ARCHIVO_ALERTA_AGENTE):
                os.remove(ARCHIVO_ALERTA_AGENTE)
        except Exception:
            logging.exception("Error en el chequeo de salud periódico.")
        await asyncio.sleep(CHEQUEO_SALUD_INTERVALO_SEG)


@app.on_event("startup")
def _startup():
    store.inicializar_db()
    cfg = filtro_mensajes.cargar_config()
    n = store.reclasificar_como_bot(lambda texto: filtro_mensajes.es_mensaje_de_bot(texto, cfg))
    if n:
        logging.info("Reclasificados %s mensajes antiguos como bot con las reglas actuales.", n)
    asyncio.create_task(_chequeo_salud_periodico())


def _requerir_token(x_agent_token: Optional[str]):
    if not AGENT_SERVICE_TOKEN:
        logging.warning("AGENT_SERVICE_TOKEN no configurado: los endpoints internos quedan sin protección.")
        return
    if x_agent_token != AGENT_SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


def _valor_json(valor):
    """
    Convierte un valor leído con pandas a un tipo serializable en JSON: numpy.int64 -> int y
    celdas vacías (NaN) -> "". Sin esto, /pending-drafts responde 500 en cuanto el borrador es
    de un teléfono que está en los CSV de leads.
    """
    if pd.isna(valor):
        return ""
    return valor.item() if hasattr(valor, "item") else valor


def _buscar_lead_por_telefono(telefono_normalizado):
    """Busca el lead en cualquiera de los dos CSV por teléfono normalizado."""
    for linea, archivo in CSVS_POR_LINEA.items():
        if not os.path.exists(archivo):
            continue
        try:
            df = pd.read_csv(archivo)
        except Exception:
            continue
        if "Telefono" not in df.columns:
            continue
        telefonos = df["Telefono"].astype(str).apply(normalizar_telefono_chile)
        coincidencias = df[telefonos == telefono_normalizado]
        if not coincidencias.empty:
            row = coincidencias.iloc[0]
            return linea, archivo, {
                campo: _valor_json(row.get(campo, ""))
                for campo in ("Evento", "Ubicacion", "Estado", "Dia_Secuencia")
            }
    return None, None, {}


_CACHE_SLOTS = {"slots": None, "timestamp": 0.0}
_CACHE_SLOTS_TTL_SEG = 120


def _obtener_slots_cacheados():
    """
    Evita golpear la Calendar API en cada mensaje entrante: refresca la lista
    de horarios libres como máximo cada _CACHE_SLOTS_TTL_SEG. Si la consulta
    falla (credenciales no configuradas, error de red, etc.), se devuelve una
    lista vacía y el agente simplemente no ofrece horarios en ese borrador.
    """
    ahora = time.time()
    if _CACHE_SLOTS["slots"] is None or ahora - _CACHE_SLOTS["timestamp"] > _CACHE_SLOTS_TTL_SEG:
        try:
            _CACHE_SLOTS["slots"] = calendar_client.obtener_slots_disponibles()
        except Exception:
            logging.exception("No se pudo consultar disponibilidad de Google Calendar.")
            _CACHE_SLOTS["slots"] = []
        _CACHE_SLOTS["timestamp"] = ahora
    return _CACHE_SLOTS["slots"]


def _extraer_mensaje_entrante(payload: dict):
    """
    Parser tolerante para el payload del webhook 'messages.upsert' de Evolution API.
    Devuelve (telefono_normalizado, texto) o (None, None) si no se reconoce el shape.
    """
    data = payload.get("data") or payload

    # Evitar procesar eventos que somos nosotros mismos enviando (fromMe).
    key = data.get("key", {}) if isinstance(data, dict) else {}
    if key.get("fromMe"):
        return None, None

    remote_jid = key.get("remoteJid", "")
    if not remote_jid:
        return None, None
    telefono_raw, _, dominio = remote_jid.partition("@")
    if dominio in ("g.us", "broadcast", "newsletter"):
        # Grupos, estados y canales: nunca son una conversación 1 a 1 con un lead.
        return None, None
    if dominio == "lid":
        # JID @lid: no es un teléfono. Se conserva completo para poder responderle
        # (Evolution v2.3.6+ acepta "<lid>@lid" como destino); normalizarlo a
        # teléfono chileno lo convertiría en un número inexistente.
        telefono = remote_jid
    else:
        telefono = normalizar_telefono_chile(telefono_raw)
    if not telefono:
        return None, None

    mensaje = data.get("message", {}) if isinstance(data, dict) else {}
    texto = (
        mensaje.get("conversation")
        or mensaje.get("extendedTextMessage", {}).get("text")
        or mensaje.get("imageMessage", {}).get("caption")
        or ""
    )
    if not texto:
        return telefono, None

    return telefono, texto


def _extraer_mensaje_id(payload: dict):
    data = payload.get("data") or payload
    key = data.get("key", {}) if isinstance(data, dict) else {}
    return key.get("id") or None


@app.get("/health")
def health():
    return {"status": "ok"}


def _validar_accion(accion, slots):
    """
    Defensa anti-alucinación: solo se acepta la acción de agendar si el inicio_iso que devolvió
    Gemini coincide EXACTO con un slot realmente ofrecido (nunca confiar en un horario "inventado").
    """
    if not accion:
        return None
    slot_valido = next((s for s in slots if s["inicio_iso"] == accion["inicio_iso"]), None)
    if not slot_valido:
        logging.warning("Gemini marcó agendar con un horario no ofrecido: %s", accion)
        return None
    accion["fin_iso"] = slot_valido["fin_iso"]
    accion["etiqueta"] = slot_valido["etiqueta"]
    return accion


@app.post("/webhook/evolution")
async def webhook_evolution(request: Request):
    payload = await request.json()
    logging.info("Webhook Evolution recibido: %s", payload)

    telefono, texto = _extraer_mensaje_entrante(payload)
    if not telefono or not texto:
        # Payload no reconocido o evento irrelevante (fromMe, sin texto, etc).
        # No es un error: Evolution manda varios tipos de eventos por el mismo webhook.
        return {"status": "ignorado"}

    # Duplicados (mismo evento entregado dos veces, p. ej. por dos instancias de Evolution).
    evo_id = _extraer_mensaje_id(payload)
    if evo_id and store.existe_mensaje_evolution(evo_id):
        return {"status": "duplicado"}

    msg_id = store.guardar_mensaje(telefono, "in", texto, evolution_message_id=evo_id)

    linea, _archivo, contexto_lead = _buscar_lead_por_telefono(telefono)

    # Filtro sin costo de tokens: bots, cierres, desconocidos, bucles... (ver filtro_mensajes.py)
    cfg = filtro_mensajes.cargar_config()
    motivo = filtro_mensajes.evaluar(
        texto,
        cfg=cfg,
        es_conocido=bool(contexto_lead) or store.tiene_mensajes_salientes(telefono),
        es_lid=telefono.endswith("@lid"),
        esperando_confirmacion=filtro_mensajes.nuestro_ultimo_mensaje_ofrecia_horario(
            store.ultimo_mensaje_saliente(telefono)
        ),
        entrantes_ultima_hora=store.contar_entrantes_desde(telefono, 60),
        repeticiones_recientes=store.contar_texto_repetido(telefono, texto, 10),
    )
    if motivo:
        store.marcar_mensaje_filtrado(msg_id, motivo)
        logging.info("Mensaje de %s filtrado (%s), sin llamar a Gemini: %r", telefono, motivo, texto[:80])
        return {"status": "filtrado", "motivo": motivo}

    # Ráfagas: si el contacto escribe varios mensajes seguidos, solo el último genera borrador.
    if cfg["activo"] and cfg["debounce_segundos"] > 0:
        await asyncio.sleep(cfg["debounce_segundos"])
        if store.hay_entrante_posterior(telefono, msg_id):
            store.marcar_mensaje_filtrado(msg_id, "rafaga")
            return {"status": "filtrado", "motivo": "rafaga"}

    historial = store.historial_conversacion(telefono, limite=20)
    slots = _obtener_slots_cacheados()

    texto_borrador, accion = generar_borrador(
        historial, contexto_lead, texto, slots_disponibles=slots, linea=linea
    )

    accion = _validar_accion(accion, slots)

    draft_id = store.crear_borrador(
        telefono, msg_id, texto_borrador,
        accion_tipo=accion["tipo"] if accion else None,
        accion_payload=accion,
    )

    return {"status": "ok", "draft_id": draft_id}


class RegenerarBorrador(BaseModel):
    instruccion: Optional[str] = None  # ajuste opcional del operador ("más corto", "no insistas"...)


@app.post("/drafts/{draft_id}/regenerate")
def regenerate_draft(draft_id: int, body: RegenerarBorrador, x_agent_token: Optional[str] = Header(default=None)):
    """
    Vuelve a redactar un borrador pendiente con Gemini, opcionalmente siguiendo una indicación del
    operador. Si Gemini no responde, el borrador actual NO se pisa con el texto de emergencia.
    """
    _requerir_token(x_agent_token)
    borrador = store.obtener_borrador(draft_id)
    if not borrador:
        raise HTTPException(status_code=404, detail="Borrador no encontrado.")
    if borrador["estado"] != "pending":
        raise HTTPException(status_code=409, detail=f"El borrador ya está en estado '{borrador['estado']}'.")

    telefono = borrador["telefono_normalizado"]
    mensaje = store.obtener_mensaje(borrador["mensaje_entrante_id"])
    entrantes = [m for m in store.historial_conversacion(telefono, limite=20) if m["direccion"] == "in"]
    texto_entrante = (mensaje or {}).get("texto") or (entrantes[-1]["texto"] if entrantes else "")

    linea, _archivo, contexto_lead = _buscar_lead_por_telefono(telefono)
    historial = store.historial_conversacion(telefono, limite=20)
    slots = _obtener_slots_cacheados()

    texto, accion = generar_borrador(
        historial, contexto_lead, texto_entrante, slots_disponibles=slots,
        instruccion_operador=body.instruccion, borrador_anterior=borrador["texto_borrador"],
        linea=linea,
    )
    if texto == _borrador_fallback(texto_entrante):
        raise HTTPException(status_code=502, detail="Gemini no respondió: el borrador no se modificó. Reintenta en un momento.")

    accion = _validar_accion(accion, slots)
    store.actualizar_borrador(
        draft_id, texto,
        accion_tipo=accion["tipo"] if accion else None,
        accion_payload=accion,
    )
    return {
        "texto_borrador": texto,
        "accion_tipo": accion["tipo"] if accion else None,
        "accion_payload": accion,
    }


@app.get("/filtered-messages")
def filtered_messages(limite: int = 50, x_agent_token: Optional[str] = Header(default=None)):
    """Últimos mensajes que NO se enviaron a Gemini y por qué (para auditar/ajustar el filtro)."""
    _requerir_token(x_agent_token)
    return store.listar_filtrados(min(max(limite, 1), 200))


@app.get("/filter-report")
def filter_report(dias: int = 7, limite: int = 200, x_agent_token: Optional[str] = Header(default=None)):
    """Resumen para la pantalla de filtros del dashboard: métricas, config vigente y mensajes descartados."""
    _requerir_token(x_agent_token)
    dias = min(max(dias, 1), 90)
    return {
        "config": filtro_mensajes.cargar_config(),
        "resumen": store.resumen_filtros(dias),
        "mensajes": store.listar_filtrados(min(max(limite, 1), 500), dias),
    }


@app.get("/replied-phones")
def replied_phones(dias: int = 90, x_agent_token: Optional[str] = Header(default=None)):
    """Teléfonos que ya respondieron: los workers pausan su secuencia automática."""
    _requerir_token(x_agent_token)
    return {"telefonos": store.telefonos_que_respondieron(min(max(dias, 1), 365))}


@app.get("/pending-drafts")
def pending_drafts(x_agent_token: Optional[str] = Header(default=None)):
    _requerir_token(x_agent_token)
    borradores = store.listar_borradores_pendientes()
    for b in borradores:
        _linea, _archivo, contexto = _buscar_lead_por_telefono(b["telefono_normalizado"])
        b["lead"] = contexto
    return borradores


class DecisionBorrador(BaseModel):
    texto_final: Optional[str] = None  # si el operador editó el borrador antes de aprobar


@app.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: int, decision: DecisionBorrador, x_agent_token: Optional[str] = Header(default=None)):
    _requerir_token(x_agent_token)
    borrador = store.obtener_borrador(draft_id)
    if not borrador:
        raise HTTPException(status_code=404, detail="Borrador no encontrado.")
    if borrador["estado"] != "pending":
        raise HTTPException(status_code=409, detail=f"Borrador ya está en estado '{borrador['estado']}'.")

    texto_envio = (decision.texto_final or borrador["texto_borrador"]).strip()
    if not texto_envio:
        raise HTTPException(status_code=400, detail="El texto a enviar no puede estar vacío.")

    if borrador.get("accion_tipo") == "agendar_cita":
        accion = json.loads(borrador["accion_payload"])
        _linea, _archivo, contexto_lead = _buscar_lead_por_telefono(borrador["telefono_normalizado"])
        try:
            resultado = calendar_client.crear_evento_demo(
                accion["inicio_iso"],
                borrador["telefono_normalizado"],
                nombre_lead=(contexto_lead or {}).get("Evento", ""),
            )
        except calendar_client.SlotNoDisponibleError:
            store.marcar_resultado_accion(draft_id, "slot_no_disponible")
            enviar_alerta_whatsapp(
                EVO_URL, EVO_TOKEN, EVO_INSTANCE, NUMERO_OPERADOR,
                f"⚠️ El horario propuesto a {borrador['telefono_normalizado']} ya no está "
                f"disponible. Revisa el borrador #{draft_id} y ofrece otro horario manualmente.",
            )
            raise HTTPException(status_code=409, detail="El horario ya no está disponible. No se envió el mensaje.")
        except Exception:
            logging.exception("Error creando evento de calendario para el draft %s.", draft_id)
            store.marcar_resultado_accion(draft_id, "error")
            raise HTTPException(status_code=502, detail="Error al crear el evento en Google Calendar. No se envió el mensaje.")
        else:
            store.registrar_cita_agendada(
                draft_id, borrador["telefono_normalizado"], resultado["event_id"],
                accion["inicio_iso"], accion["fin_iso"],
            )
            store.marcar_resultado_accion(draft_id, "creado")

    enviado = _evo_enviar_mensaje_texto(
        EVO_URL, EVO_TOKEN, EVO_INSTANCE, borrador["telefono_normalizado"], texto_envio
    )
    if not enviado:
        store.marcar_borrador(draft_id, "approved")
        raise HTTPException(status_code=502, detail="Aprobado pero falló el envío por Evolution API. Reintentar.")

    store.guardar_mensaje(borrador["telefono_normalizado"], "out", texto_envio)
    store.marcar_borrador(draft_id, "sent")
    return {"status": "enviado"}


@app.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: int, x_agent_token: Optional[str] = Header(default=None)):
    _requerir_token(x_agent_token)
    borrador = store.obtener_borrador(draft_id)
    if not borrador:
        raise HTTPException(status_code=404, detail="Borrador no encontrado.")
    store.marcar_borrador(draft_id, "rejected")
    return {"status": "rechazado"}
