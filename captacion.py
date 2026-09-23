"""
Decide, para cada negocio que devuelve Google Maps, qué número de WhatsApp usar (si hay uno):

1. Si el teléfono de Google es un celular, se usa ese.
2. Si es un fijo (o no hay), y el negocio tiene sitio web, se buscan celulares/links de WhatsApp
   en su web ("rescate"): muchos negocios de barrio publican un fijo en Google pero atienden
   por un celular con WhatsApp.
3. Todos los candidatos se verifican en Evolution (¿tiene WhatsApp?) antes de agregarlos.

Compartido por los workers de clínicas y almacenes.
"""
import filtros_leads
import web_scraper
from evo_client import es_movil_chileno, normalizar_telefono_chile, verificar_whatsapp

# Tope de sitios web a revisar por página de resultados (acota el tiempo del ciclo).
MAX_SITIOS_RESCATE = 8


def preparar_candidatos(resultados, tels_en_base, linea, descartes, necesita_email=False):
    """
    Devuelve una lista de candidatos {place, telefonos, origen, email} donde `telefonos` son
    celulares a verificar en orden de preferencia. `descartes` (dict) acumula los motivos.
    Actualiza `tels_en_base` con los teléfonos ya tomados para no repetir dentro del mismo lote.
    """
    candidatos = []
    rescates = 0
    for place in resultados:
        if filtros_leads.es_cadena(place.get("title", ""), linea):
            descartes["cadena"] += 1
            continue

        tel_google = normalizar_telefono_chile(place.get("phone", ""))
        website = place.get("website") or ""
        telefonos, origen, email = [], "google", ""

        if es_movil_chileno(tel_google):
            telefonos = [tel_google]
        elif website and rescates < MAX_SITIOS_RESCATE:
            rescates += 1
            contactos = web_scraper.obtener_contactos(website, necesita_email=necesita_email, necesita_movil=True)
            telefonos, origen, email = contactos["moviles"][:2], "web", contactos["email"]
            if not telefonos:
                descartes["sin_movil"] += 1
                continue
        else:
            descartes["sin_movil"] += 1  # fijo/600/800 sin sitio web donde buscar un celular
            continue

        telefonos = [t for t in telefonos if t[-9:] not in tels_en_base]
        if not telefonos:
            descartes["duplicado"] += 1
            continue
        for t in telefonos:
            tels_en_base.add(t[-9:])
        candidatos.append({"place": place, "telefonos": telefonos, "origen": origen, "email": email})
    return candidatos


def confirmar_con_whatsapp(candidatos, evo_url, evo_token, evo_instance, descartes):
    """
    Verifica en Evolution y devuelve [(candidato, telefono_elegido)] solo con números que
    tienen WhatsApp. Si Evolution no responde, se acepta el primero sin verificar.
    """
    todos = [t for c in candidatos for t in c["telefonos"]]
    existe = verificar_whatsapp(evo_url, evo_token, evo_instance, todos)
    confirmados = []
    for c in candidatos:
        elegido = next((t for t in c["telefonos"] if existe.get(t) is not False), None)
        if elegido is None:
            descartes["sin_whatsapp"] += 1
            continue
        confirmados.append((c, elegido))
    return confirmados
