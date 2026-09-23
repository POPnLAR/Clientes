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
}


def _limpio(texto):
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if not unicodedata.combining(c)).lower()).strip()


def es_cadena(nombre, linea):
    """linea: 'almacenes' o 'clinicas'."""
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
        r"verduleria|despensa|grocery|convenience|liquor|supermarket"
    ),
    "clinicas": (
        r"estetic|belleza|beauty|\bspa\b|cosmet|depila|laser|\bpiel\b|skin|facial|corporal|masaje|"
        r"micropigment|cejas|pestan|lash|clinica|medic|dermat|salon|salud|botox|rellen|bienestar|"
        r"wellness|peluquer|\bunas\b|nail"
    ),
}
_RUBRO_NO = {
    "almacenes": (
        r"\btelas?\b|textil|cortina|\bropa\b|cristaleria|bodega|storage|guardamuebles|outlet|"
        r"mayorista|restaurante|restaurant|ferreteria|muebles|decoracion|tienda de ropa"
    ),
    "clinicas": r"\bhotel\b|inmobiliaria|ferreteria|restaurante|restaurant",
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
_RUBRO_ESTRICTO = {"almacenes": True, "clinicas": False}


def categoria_relevante(place, linea):
    """True si el negocio corresponde al rubro de la línea ('almacenes' o 'clinicas')."""
    si, no = _RUBRO_SI.get(linea), _RUBRO_NO.get(linea)
    if not si:
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
