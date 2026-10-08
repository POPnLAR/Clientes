"""
Reglas (determinísticas, sin tokens) que deciden si el agente puede responder SOLO, sin esperar
la aprobación del operador, y detección de rechazos para no volver a contactar a esa persona.

Solo se envía solo lo de menor riesgo: respuestas informativas. Todo lo que agenda una cita, habla
de dinero, o requiere criterio humano queda como borrador pendiente. El modelo clasifica su propia
respuesta, pero NO se confía solo en eso: aquí se revisan además el texto que va a salir y el mensaje
que escribió el prospecto.
"""
import re
import unicodedata

# Tope de respuestas automáticas a un mismo contacto en 24 h (corta bucles con bots que pasen el filtro).
MAX_AUTO_POR_CONTACTO_24H = 3
# Una respuesta más larga que esto se revisa a mano (el prompt pide 3-4 oraciones).
MAX_LARGO_RESPUESTA = 700


def _norm(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    sin = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", sin).strip()


# El prospecto rechaza claramente: no se le vuelve a escribir desde la secuencia automática.
_RECHAZO = re.compile(
    r"\bno (me )?(interesa|interesan|quiero|necesito|molesten|molestes|molesteis)\b|"
    r"\bno gracias\b|\bno,? gracias\b|"
    r"\bno (me )?(escriban|escribas|contacten|contactes|manden|mandes|envien|envies|llamen|llames)\b|"
    r"\bdejen de\b|\bdeja de (escribir|mandar|enviar)\b|\bno insistan?\b|"
    r"\bya no (quiero|me interesa|necesito|tengo (ese|el|este|mi) (local|negocio|almacen|clinica|centro))\b|"
    r"\b(saquen|saca|borren|borra|eliminen|elimina)(me)? (mi|este) (numero|contacto)\b|"
    r"\bdar(me)? de baja\b|\bcerramos\b|\bcerre el (local|negocio)\b|\bnumero equivocado\b|"
    r"\bequivocaron de numero\b"
)

# Necesita a una persona: molestia, reclamo, asunto legal, pide hablar con alguien.
_REQUIERE_HUMANO = re.compile(
    r"\breclamo\b|\bdenuncia|\babogad|\bestafa|\bspam\b|\bbasura\b|\bmolest(o|a|os|as|ia|ando)\b|"
    r"\bhartaz|\bharto\b|\bharta\b|\bpesad[oa]s?\b|\bacoso|\bbloque(o|are|ar)\b|\bsuperintendencia\b|"
    r"\bsernac\b|\bpolicia\b|\bcarabineros\b|"
    r"\bhablar con (alguien|una persona|un humano|rodrigo|el dueno|el encargado|un ejecutivo)\b|"
    r"\bpersona real\b|\bun humano\b|\bquien (eres|es usted)\b|\beres (un )?(bot|robot)\b|"
    r"\bcomo (conseguiste|obtuviste|tienes) (mi|este) (numero|contacto)\b"
)

# Dinero o compromisos en el texto que SALDRÍA: eso nunca se envía solo.
_DINERO = re.compile(
    r"\$|\bclp\b|\bpesos?\b|\buf\b|\b\d{1,3}(\.\d{3})+\b|\b\d+ ?(mil|lucas?)\b|"
    r"\bdescuento|\bpromo(cion|ciones)?\b|\boferta|\b\d+ ?%|\bporcentaje\b|\bcobr(o|a|amos|an)\b|"
    r"\b(iva|boleta|factura|contrato)\b"
)


def es_rechazo(texto_entrante):
    return bool(_RECHAZO.search(_norm(texto_entrante)))


def requiere_humano(texto_entrante):
    return bool(_REQUIERE_HUMANO.search(_norm(texto_entrante)))


def respuesta_menciona_dinero(texto_respuesta):
    return bool(_DINERO.search(_norm(texto_respuesta)))


def decidir(*, modo_activo, clase, accion, texto_respuesta, texto_entrante, auto_enviados_24h):
    """
    Devuelve (enviar_solo: bool, motivo: str). `motivo` explica por qué NO se envía solo (o "ok").
    clase: la clasificación del modelo; accion: la acción de agendar (o None).
    """
    if not modo_activo:
        return False, "respuesta automática apagada"
    if accion:
        return False, "agenda una cita"
    if clase == "fallback":
        return False, "el modelo no respondió (texto de emergencia)"
    if requiere_humano(texto_entrante):
        return False, "el prospecto necesita una persona"
    rechazo = es_rechazo(texto_entrante)
    # Un rechazo se agradece con cortesía sin importar cómo lo clasificó el modelo; todo lo demás
    # debe venir clasificado como informativa.
    if not rechazo and clase != "informativa":
        return False, f"clasificada como «{clase or 'sin clase'}»"
    if respuesta_menciona_dinero(texto_respuesta):
        return False, "la respuesta menciona dinero, descuentos o condiciones"
    if len(texto_respuesta or "") > MAX_LARGO_RESPUESTA:
        return False, "respuesta demasiado larga"
    if not (texto_respuesta or "").strip():
        return False, "respuesta vacía"
    if auto_enviados_24h >= MAX_AUTO_POR_CONTACTO_24H:
        return False, f"ya se le respondió solo {auto_enviados_24h} veces en 24 h"
    return True, "ok"
