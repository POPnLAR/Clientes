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
    # Mutualidades y organismos públicos no son consultoras: son proveedores del sistema o fiscalizan.
    "prevencion": (
        r"\bachs\b|asociacion chilena de seguridad|mutual de seguridad|\bist\b|instituto de seguridad|"
        r"\bisl\b|mutualidad|sernageomin|\bseremi\b|direccion del trabajo|inspeccion del trabajo|"
        r"suseso|\bsence\b|municipalidad|bomberos|carabineros|\bhospital\b|\bcesfam\b|"
        r"universidad|\bduoc\b|\binacap\b|\bsanto tomas\b|colegio|liceo"
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
    "prevencion": (
        r"prevencion|riesgo|seguridad (laboral|ocupacional|industrial|y salud|minera)|salud ocupacional|"
        r"\bsso\b|\bsst\b|\bhse\b|hseq|higiene|ergonom"
    ),
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
    # Seguridad privada, venta de equipos y rubros ajenos que Google mezcla con "seguridad" o "riesgo".
    "prevencion": (
        r"seguridad privada|guardias?|alarma|cerrajer|camara|extintor|ferreteria|epp\b|"
        r"venta de (ropa|equipos|implementos)|implementos de seguridad|ropa de trabajo|"
        r"corredora? de seguros|seguros|inmobiliaria|restaurant|restaurante|hotel|"
        r"transporte|taller|mecanic|abogad|contab|colegio|jardin infantil"
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
# Prevención: lista estricta: el nombre o la categoría deben hablar de prevención, riesgos, seguridad
# laboral u ocupacional, HSE/SSO o higiene. "Consultora" o "capacitación" por sí solos NO bastan (dejaban
# pasar consultoras informáticas y centros de formación). Es B2B y de volumen bajo: preferimos perder
# algún lead antes que escribirle a un negocio ajeno desde el número compartido.
_RUBRO_ESTRICTO = {"almacenes": True, "clinicas": False, "prevencion": True}


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
