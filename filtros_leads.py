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
