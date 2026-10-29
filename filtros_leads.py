"""
Filtros de calidad para los leads que trae la búsqueda de Google Maps: descarta
cadenas y organizaciones grandes (no son el público objetivo y suelen compartir
un número central de call center).
"""
import re
import unicodedata

_PATRONES = {
    "almacenes": (
        r"\blider\b|\bwalmart\b|santa isabel|\bunimarc\b|\bjumbo\b|\btottus\b|\bacuenta\b|a cuenta|"
        r"\boxxo\b|ok market|mayorista 10|super 10|\balvi\b|\bcopec\b|\bpronto\b|\bshell\b|"
        r"\bsodimac\b|starbucks|mcdonald|\bsubway\b"
    ),
    "clinicas": (
        r"clinica (alemana|las condes|santa maria|davila|indisa|meds|bicentenario|vespucio)|"
        r"red ?salud|megasalud|integramedica|\bbupa\b|cruz verde|ahumada|salcobrand|"
        r"\bhospital\b|\bcesfam\b|consultorio|universidad|\bduoc\b|\binacap\b"
    ),
    # Línea "prevencion" (clave histórica): hoy es la de páginas web para negocios sin sitio. Cadenas,
    # bancos y organismos públicos no son el público objetivo: no deciden por su cuenta y comparten números.
    "prevencion": (
        r"\blider\b|\bwalmart\b|santa isabel|\bunimarc\b|\bjumbo\b|\btottus\b|\bsodimac\b|\beasy\b|"
        r"falabella|\bparis\b|\bripley\b|\bhites\b|cruz verde|ahumada|salcobrand|mcdonald|starbucks|"
        r"burger king|\bkfc\b|pizza hut|telepizza|doggis|juan maestro|\bcopec\b|\bshell\b|petrobras|"
        r"\bbanco\b|banco ?estado|\bbci\b|santander|scotiabank|\bentel\b|movistar|\bclaro\b|\bwom\b|"
        r"\bcorreos\b|municipalidad|\bseremi\b|bomberos|carabineros|\bhospital\b|\bcesfam\b|consultorio|"
        r"universidad|\bduoc\b|\binacap\b|\bsanto tomas\b|colegio|liceo|jardin infantil|escuela"
    ),
}


def _limpio(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if not unicodedata.combining(c)).lower()).strip()


def es_cadena(nombre, linea):
    """linea: 'almacenes', 'clinicas' o 'prevencion'."""
    patron = _PATRONES.get(linea)
    return bool(patron and re.search(patron, _limpio(nombre)))


# --- Relevancia de rubro -------------------------------------------------------------------
# Que un negocio tenga celular con WhatsApp no basta: el nombre de un rubro puede ser ambiguo
# ("almacén" también es bodega o mayorista textil) y Google devuelve lo que sea. Se exige que la
# categoría de Google Maps (o, si no viene, el nombre) corresponda al rubro y no a uno excluido.
_RUBRO_SI = {
    "almacenes": (
        r"minimarket|mini ?market|mini ?mercado|minimercado|\balmacen(es)?\b|abarrotes|comestibles|"
        r"conveniencia|supermercado|botilleria|licoreria|bebidas alcoholicas|emporio|fruteria|"
        r"verduleria|despensa|grocery|convenience|liquor|supermarket|"
        # Categorías reales que Google Maps usa en Chile para los almacenes de barrio:
        r"alimentacion|tienda general|mercado"
    ),
    "clinicas": (
        r"estetic|belleza|beauty|\bspa\b|cosmet|depila|laser|\bpiel\b|skin|facial|corporal|masaje|"
        r"micropigment|cejas|pestan|lash|clinica|medic|dermat|salon|salud|botox|rellen|bienestar|"
        r"wellness|peluquer|\bunas\b|nail"
    ),
    # Sin lista de rubros (ver _RUBRO_ESTRICTO): vale cualquier negocio que atienda público.
    "prevencion": None,
}
_RUBRO_NO = {
    "almacenes": (
        r"\btelas?\b|textil|cortina|\bropa\b|cristaleria|bodega|storage|guardamuebles|outlet|"
        r"mayorista|restaurante|restaurant|ferreteria|muebles|decoracion|tienda de ropa|"
        r"grandes almacenes|centro comercial|mall|trasteros|almacenamiento|minibodega|"
        r"electrodomesticos|perfumeria|jugueteria|zapateria|tienda de moviles|tienda de vinos"
    ),
    # Rubros ajenos que suelen colarse en las búsquedas de estética (p. ej. "Clínica Veterinaria").
    "clinicas": (
        r"\bhotel\b|inmobiliaria|ferreteria|restaurante|restaurant|"
        r"veterin|mascota|optica|farmacia|panaderia|supermercado|abogad|contab|mecanic"
    ),
    # Lugares que no son un negocio al que ofrecerle una web (instituciones, espacios públicos, servicios).
    "prevencion": (
        r"\bbanco\b|cajero|iglesia|parroquia|junta de vecinos|club deportivo|estadio|\bplaza\b|\bparque\b|"
        r"cementerio|notaria|juzgado|registro civil|comisaria|cuartel|gobierno|oficina de correos|"
        r"estacion de servicio|aeropuerto|terminal de buses|centro comercial|\bmall\b|"
        r"sede social|organizacion|fundacion|corporacion"
    ),
}


def texto_categoria(place):
    """Categoría(s) de Google Maps del negocio (campos `type` y `types` de SerpAPI), en texto."""
    tipos = place.get("types")
    partes = [str(place.get("type") or "")]
    if isinstance(tipos, (list, tuple)):
        partes += [str(t) for t in tipos]
    elif tipos:
        partes.append(str(tipos))
    return " ".join(p for p in partes if p)


# Almacenes: lista estricta (el rubro es ambiguo y Google devuelve bodegas, telas, mayoristas).
# Clínicas: solo se excluye lo claramente ajeno; la lista estricta podría dejar fuera negocios
# legítimos mientras no se hayan visto las categorías reales que devuelve Google para Chile.
#
# "prevencion" (páginas web): el rubro lo fijan los términos de búsqueda y el criterio real es que no
# tenga web propia (ver tiene_web_propia); aquí solo se excluye lo que no es un negocio.
_RUBRO_ESTRICTO = {"almacenes": True, "clinicas": False, "prevencion": False}

# Redes y enlaces-puente: un negocio que en Google solo muestra uno de estos NO tiene página web propia.
_DOMINIOS_NO_WEB = (
    "facebook.com", "fb.com", "fb.me", "instagram.com", "wa.me", "whatsapp.com", "linktr.ee",
    "tiktok.com", "twitter.com", "x.com", "youtube.com", "youtu.be", "google.com",
    "goo.gl", "g.page", "business.site", "wixsite.com", "negocio.site", "bio.link", "beacons.ai",
    "mercadolibre.cl", "mercadolibre.com", "olx.cl", "yapo.cl", "booksy.com", "agendapro.com",
)


def tiene_web_propia(place):
    """True si el negocio publica en Google Maps un sitio web propio (no solo una red social o un enlace-puente)."""
    sitio = _limpio(place.get("website") or "")
    if not sitio:
        return False
    host = re.sub(r"^https?://", "", sitio).split("/")[0].split("?")[0]
    return not any(host == d or host.endswith("." + d) for d in _DOMINIOS_NO_WEB)


def categoria_relevante(place, linea):
    """True si el negocio corresponde al rubro de la línea ('almacenes', 'clinicas' o 'prevencion')."""
    si, no = _RUBRO_SI.get(linea), _RUBRO_NO.get(linea)
    if not si and not no:
        return True
    categoria = _limpio(texto_categoria(place))
    titulo = _limpio(place.get("title", ""))
    conjunto = f"{titulo} {categoria}"
    if no and re.search(no, conjunto):
        return False
    if not _RUBRO_ESTRICTO.get(linea, True):
        return True
    # La categoría manda; si Google no la trae, se decide por el nombre.
    return bool(re.search(si, categoria if categoria else titulo)) or bool(re.search(si, titulo))
