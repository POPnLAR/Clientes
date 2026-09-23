"""
Cliente de SerpAPI (Google Maps) compartido por los workers de clínicas y almacenes:
presupuesto mensual de búsquedas y búsqueda paginada.

Cada línea lleva su propio archivo de presupuesto y su propio límite (repartiendo el
cupo mensual total entre ambas) para que los dos workflows nunca escriban el mismo
archivo. Cada página de resultados consume una búsqueda del cupo.
"""
import calendar
import json
import logging
import math
import os

import requests

PAGINA_TAMANO = 20  # resultados por página de Google Maps en SerpAPI
MAX_PAGINAS = int(os.getenv("SERP_MAX_PAGINAS", "3"))
# Se pide la página siguiente solo si la actual aportó al menos esta cantidad de leads útiles.
MIN_UTILES_PARA_PAGINAR = int(os.getenv("SERP_MIN_UTILES_PARA_PAGINAR", "4"))


def _cargar_presupuesto(archivo):
    if os.path.exists(archivo):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.exception("No se pudo leer %s, se reinicia el contador.", archivo)
    return {"mes": None, "usadas": 0}


def _guardar_presupuesto(archivo, estado):
    try:
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("No se pudo escribir %s.", archivo)


def reservar_busqueda(archivo, limite_mensual, ahora):
    """
    Reparte el cupo mensual de forma pareja a lo largo del mes (en vez de gastarlo en los
    primeros días). Si hay cupo para el día de hoy lo reserva y devuelve True.
    """
    mes_actual = ahora.strftime("%Y-%m")
    estado = _cargar_presupuesto(archivo)
    if estado.get("mes") != mes_actual:
        estado = {"mes": mes_actual, "usadas": 0}

    dias_en_mes = calendar.monthrange(ahora.year, ahora.month)[1]
    permitido_hasta_hoy = min(limite_mensual, math.ceil(ahora.day * limite_mensual / dias_en_mes))

    if estado["usadas"] >= permitido_hasta_hoy:
        _guardar_presupuesto(archivo, estado)
        print(f"💸 Cupo de SerpAPI del día agotado ({estado['usadas']}/{permitido_hasta_hoy} permitidas "
              f"a esta altura del mes, límite mensual {limite_mensual}).")
        return False

    estado["usadas"] += 1
    _guardar_presupuesto(archivo, estado)
    print(f"💳 Presupuesto SerpAPI: {estado['usadas']}/{limite_mensual} usadas este mes "
          f"({permitido_hasta_hoy} permitidas a esta altura del mes).")
    return True


def buscar_pagina(query, api_key, start=0, timeout=30):
    """
    Una página de Google Maps (1 crédito). Devuelve (estado, resultados) con estado en:
    "ok" | "sin_resultados" | "cuota_agotada" | "error_api".
    """
    params = {
        "engine": "google_maps",
        "type": "search",
        "q": query,
        "hl": "es",
        "gl": "cl",
        "start": start,
        "api_key": api_key,
    }
    try:
        response = requests.get("https://serpapi.com/search", params=params, timeout=timeout)
        print(f"🔎 SerpAPI status: {response.status_code} (página {start // PAGINA_TAMANO + 1})")
        data = response.json()
        if "error" in data:
            if "hasn't returned any results" in str(data["error"]).lower():
                return "sin_resultados", []
            # SerpAPI devuelve HTTP 200 con {"error": "..."} en cuota agotada / api_key inválida.
            print(f"🚫 SerpAPI error: {data['error']}")
            logging.error("SerpAPI error: %s", data["error"])
            return "cuota_agotada", []
        resultados = data.get("local_results", []) or []
        print(f"🔎 SerpAPI local_results: {len(resultados)}")
        return ("ok" if resultados else "sin_resultados"), resultados
    except Exception:
        logging.exception("Error al consultar SerpAPI.")
        return "error_api", []


def buscar_paginas(query, api_key, archivo_presupuesto, limite_mensual, ahora, procesar_pagina,
                   max_paginas=None, min_utiles=None):
    """
    Pide páginas de Google Maps una tras otra. `procesar_pagina(resultados)` agrega los
    leads de esa página y devuelve cuántos fueron útiles; la búsqueda continúa solo si
    la página vino llena, aportó al menos `min_utiles` leads y todavía hay cupo.
    Devuelve (estado, paginas_usadas): el de la primera página manda ("presupuesto_agotado"
    si no había cupo); un problema en una página posterior no invalida lo ya obtenido.
    """
    max_paginas = max_paginas or MAX_PAGINAS
    min_utiles = MIN_UTILES_PARA_PAGINAR if min_utiles is None else min_utiles
    paginas = 0
    estado_final = "sin_resultados"
    for n in range(max_paginas):
        if not reservar_busqueda(archivo_presupuesto, limite_mensual, ahora):
            return ("presupuesto_agotado" if n == 0 else estado_final), paginas
        estado, resultados = buscar_pagina(query, api_key, start=n * PAGINA_TAMANO)
        paginas += 1
        if n == 0:
            estado_final = estado
        if estado != "ok":
            break
        estado_final = "ok"
        utiles = procesar_pagina(resultados)
        if len(resultados) < PAGINA_TAMANO or utiles < min_utiles:
            break
        print(f"📄 La página aportó {utiles} leads útiles: se pide la siguiente.")
    return estado_final, paginas
