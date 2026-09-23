"""
Extrae datos de contacto (emails y celulares de WhatsApp) desde el sitio web de un negocio.
Solo usa peticiones HTTP directas: no consume búsquedas de SerpAPI.
"""
import re

import requests

PAGINAS_CONTACTO = ["/contacto", "/contact", "/contactenos", "/nosotros", "/about", "/sobre-nosotros"]
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Links de WhatsApp: la señal más fiable de que el número es un WhatsApp del negocio.
_RE_LINK_WA = re.compile(
    r"(?:wa\.me/|(?:api|web)\.whatsapp\.com/send/?\?[^\"'\s<>]*?phone=|whatsapp://send\?[^\"'\s<>]*?phone=)\+?(\d{9,13})",
    re.IGNORECASE,
)
_RE_LINK_TEL = re.compile(r"tel:\+?([\d\s\-().]{9,20})", re.IGNORECASE)
# En texto suelto solo se acepta con prefijo +56 (evita confundir RUT, precios o códigos).
_RE_MOVIL_TEXTO = re.compile(r"\+\s?56[\s.-]?9[\s.-]?\d{4}[\s.-]?\d{4}")
_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _a_movil(digitos):
    """Normaliza a 569XXXXXXXX si es un celular chileno; si no, None."""
    d = "".join(filter(str.isdigit, digitos))
    if d.startswith("56") and len(d) == 11 and d[2] == "9":
        return d
    if len(d) == 9 and d.startswith("9"):
        return "56" + d
    return None


def extraer_moviles(html):
    """Celulares chilenos encontrados en el HTML, los de links de WhatsApp primero, sin repetir."""
    encontrados = []
    for patron, tomar in ((_RE_LINK_WA, 1), (_RE_LINK_TEL, 1), (_RE_MOVIL_TEXTO, 0)):
        for m in patron.finditer(html):
            movil = _a_movil(m.group(tomar))
            if movil and movil not in encontrados:
                encontrados.append(movil)
    return encontrados


def extraer_email(html):
    emails = [
        e for e in _RE_EMAIL.findall(html)
        if not e.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))
    ]
    if not emails:
        return ""
    prioritarios = [e for e in emails if any(p in e.lower() for p in ["contacto", "info", "ventas"])]
    return (prioritarios[0] if prioritarios else emails[0]).lower()


def obtener_contactos(url, necesita_email=True, necesita_movil=True):
    """
    Revisa la home y, mientras falte algo de lo pedido, las páginas de contacto típicas.
    Devuelve {"email": str, "moviles": [569XXXXXXXX, ...]} (vacíos si no encontró nada).
    """
    resultado = {"email": "", "moviles": []}
    if not url or not str(url).startswith("http"):
        return resultado
    base = str(url).rstrip("/")

    def falta():
        return (necesita_email and not resultado["email"]) or (necesita_movil and not resultado["moviles"])

    for i, ruta in enumerate([""] + PAGINAS_CONTACTO):
        if i > 0 and not falta():
            break
        try:
            r = requests.get(base + ruta, headers=_HEADERS, timeout=12 if i == 0 else 8)
            if r.status_code != 200:
                continue
            if necesita_email and not resultado["email"]:
                resultado["email"] = extraer_email(r.text)
            if necesita_movil:
                for m in extraer_moviles(r.text):
                    if m not in resultado["moviles"]:
                        resultado["moviles"].append(m)
        except Exception:
            continue
    return resultado
