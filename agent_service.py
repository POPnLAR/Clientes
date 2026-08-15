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
from datetime import datetime
from typing import Optional

import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

import conversation_store as store
from evo_client import (
    enviar_alerta_whatsapp,
    enviar_mensaje_texto as _evo_enviar_mensaje_texto,
    normalizar_telefono_chile,
    verificar_estado_conexion,
)
from gemini_client import generar_borrador

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
    asyncio.create_task(_chequeo_salud_periodico())


def _requerir_token(x_agent_token: Optional[str]):
    if not AGENT_SERVICE_TOKEN:
        logging.warning("AGENT_SERVICE_TOKEN no configurado: los endpoints internos quedan sin protección.")
        return
    if x_agent_token != AGENT_SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido.")


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
                "Evento": row.get("Evento", ""),
                "Ubicacion": row.get("Ubicacion", ""),
                "Estado": row.get("Estado", ""),
                "Dia_Secuencia": row.get("Dia_Secuencia", ""),
            }
    return None, None, {}


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
    telefono_raw = remote_jid.split("@")[0]
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/evolution")
async def webhook_evolution(request: Request):
    payload = await request.json()
    logging.info("Webhook Evolution recibido: %s", payload)

    telefono, texto = _extraer_mensaje_entrante(payload)
    if not telefono or not texto:
        # Payload no reconocido o evento irrelevante (fromMe, sin texto, etc).
        # No es un error: Evolution manda varios tipos de eventos por el mismo webhook.
        return {"status": "ignorado"}

    msg_id = store.guardar_mensaje(telefono, "in", texto)

    _linea, _archivo, contexto_lead = _buscar_lead_por_telefono(telefono)
    historial = store.historial_conversacion(telefono, limite=20)

    borrador = generar_borrador(historial, contexto_lead, texto)
    draft_id = store.crear_borrador(telefono, msg_id, borrador)

    return {"status": "ok", "draft_id": draft_id}


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
