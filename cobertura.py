"""
Barrido sistemático comuna x rubro, compartido por los workers de clínicas y almacenes.

Cada línea lleva su propio archivo de estado (JSON) con las combinaciones que aún
faltan por buscar en la "vuelta" actual. Al agotarlas comienza una vuelta nueva.
"""
import json
import logging
import os
import random

# Las 52 comunas de la Región Metropolitana.
COMUNAS_RM = [
    # Provincia de Santiago (32)
    "Santiago Centro", "Cerrillos", "Cerro Navia", "Conchalí", "El Bosque",
    "Estación Central", "Huechuraba", "Independencia", "La Cisterna", "La Florida",
    "La Granja", "La Pintana", "La Reina", "Las Condes", "Lo Barnechea",
    "Lo Espejo", "Lo Prado", "Macul", "Maipú", "Ñuñoa",
    "Pedro Aguirre Cerda", "Peñalolén", "Providencia", "Pudahuel", "Quilicura",
    "Quinta Normal", "Recoleta", "Renca", "San Joaquín", "San Miguel",
    "San Ramón", "Vitacura",
    # Provincia Cordillera (3)
    "Puente Alto", "Pirque", "San José de Maipo",
    # Provincia Chacabuco (3)
    "Colina", "Lampa", "Til Til",
    # Provincia Maipo (4)
    "San Bernardo", "Buin", "Paine", "Calera de Tango",
    # Provincia Melipilla (5)
    "Melipilla", "Alhué", "Curacaví", "María Pinto", "San Pedro",
    # Provincia Talagante (5)
    "Talagante", "El Monte", "Isla de Maipo", "Padre Hurtado", "Peñaflor",
]

# Regiones II (Antofagasta), III (Atacama) y IV (Coquimbo): zona minera donde trabajan las
# consultoras de prevención de riesgos. Se omiten las comunas casi despobladas (p. ej. Ollagüe).
COMUNAS_NORTE = [
    # II Región de Antofagasta
    "Antofagasta", "Calama", "Mejillones", "Tocopilla", "Taltal", "María Elena",
    "Sierra Gorda", "San Pedro de Atacama",
    # III Región de Atacama
    "Copiapó", "Caldera", "Tierra Amarilla", "Chañaral", "Diego de Almagro",
    "Vallenar", "Huasco", "Freirina", "Alto del Carmen",
    # IV Región de Coquimbo
    "La Serena", "Coquimbo", "Ovalle", "Illapel", "Salamanca", "Los Vilos",
    "Andacollo", "Vicuña", "Monte Patria", "Combarbalá", "Canela", "Punitaqui",
]

# Orden de barrido del norte: primero las ciudades donde están las consultoras, al final las comunas
# pequeñas (que suelen devolver 0 resultados y gastan un crédito de SerpAPI cada una). Las comunas que
# no figuren acá van al final de la vuelta. Dentro de cada grupo el orden sigue siendo aleatorio.
PRIORIDAD_NORTE = [
    ["Antofagasta", "Calama", "Copiapó", "La Serena", "Coquimbo", "Ovalle", "Vallenar"],
    ["Caldera", "Mejillones", "Tocopilla", "Diego de Almagro", "Chañaral", "Illapel", "Vicuña", "Los Vilos"],
]

# Todo Chile: comunas de la Región Metropolitana más las principales ciudades de las otras 15 regiones.
# No son las 346 comunas a propósito: las pequeñas devuelven pocos resultados y cada búsqueda gasta un
# crédito de SerpAPI. Se pueden agregar más comunas a esta lista cuando haga falta.
CIUDADES_REGIONES = [
    # XV Arica y Parinacota, I Tarapacá
    "Arica", "Iquique", "Alto Hospicio",
    # II Antofagasta, III Atacama, IV Coquimbo
    "Antofagasta", "Calama", "Copiapó", "Vallenar", "La Serena", "Coquimbo", "Ovalle",
    # V Valparaíso
    "Valparaíso", "Viña del Mar", "Quilpué", "Villa Alemana", "San Antonio", "Quillota", "Los Andes", "San Felipe",
    # VI O'Higgins, VII Maule
    "Rancagua", "San Fernando", "Rengo", "Talca", "Curicó", "Linares", "Constitución",
    # XVI Ñuble, VIII Biobío
    "Chillán", "Concepción", "Talcahuano", "San Pedro de la Paz", "Chiguayante", "Los Ángeles", "Coronel", "Lota",
    # IX Araucanía, XIV Los Ríos
    "Temuco", "Padre Las Casas", "Villarrica", "Angol", "Valdivia", "La Unión",
    # X Los Lagos
    "Puerto Montt", "Osorno", "Puerto Varas", "Castro", "Ancud",
    # XI Aysén, XII Magallanes
    "Coyhaique", "Punta Arenas", "Puerto Natales",
]
COMUNAS_NACIONAL = COMUNAS_RM + CIUDADES_REGIONES

# Orden de barrido nacional: primero las ciudades más grandes (más negocios por búsqueda), después el resto.
PRIORIDAD_NACIONAL = [
    ["Santiago Centro", "Providencia", "Las Condes", "Maipú", "Puente Alto", "La Florida", "Ñuñoa",
     "Antofagasta", "Valparaíso", "Viña del Mar", "Concepción", "La Serena", "Temuco", "Rancagua",
     "Iquique", "Puerto Montt", "Talca", "Arica", "Chillán"],
    ["San Bernardo", "Calama", "Copiapó", "Osorno", "Valdivia", "Punta Arenas", "Coquimbo", "Curicó",
     "Los Ángeles", "Talcahuano", "Quilpué", "Ovalle", "Alto Hospicio", "Peñalolén", "Pudahuel",
     "Quilicura", "Vitacura", "La Reina", "Macul", "San Miguel", "Estación Central", "Recoleta"],
]


def _cargar(archivo):
    if os.path.exists(archivo):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.exception("No se pudo leer %s, se reconstruye desde cero.", archivo)
    return None


def _guardar(archivo, estado):
    try:
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("No se pudo escribir %s.", archivo)


def _nueva_vuelta(comunas, terminos, vuelta_anterior=0):
    combos = [[c, t] for c in comunas for t in terminos]
    random.shuffle(combos)
    return {"pendientes": combos, "vuelta": vuelta_anterior + 1, "terminos": list(terminos)}


def _ordenar_por_prioridad(pendientes, prioridad):
    """
    Ordena las combinaciones [comuna, término] por grupo de prioridad (el índice del grupo de la comuna;
    las no listadas van al final). El orden es estable: dentro de un grupo se conserva el que ya tenían,
    así que aplicarlo varias veces sobre el mismo estado no cambia nada.
    """
    rango = {c: i for i, grupo in enumerate(prioridad) for c in grupo}
    return sorted(pendientes, key=lambda p: rango.get(p[0], len(prioridad)))


def siguiente(archivo, comunas, terminos, terminos_previos=(), prioridad=None):
    """
    Devuelve (zona, termino) a buscar, sin marcarla como cubierta (eso lo hace
    marcar_cubierto una vez que la búsqueda corrió de verdad). `terminos_previos` son
    los rubros que ya existían antes de que el estado guardara su lista de rubros
    (para no confundirlos con rubros nuevos al migrar).
    Si se agregan rubros a `terminos` con una vuelta en curso, sus combinaciones se
    suman a las pendientes sin reiniciar la vuelta.
    `prioridad` (opcional): lista de grupos de comunas a barrer primero, en ese orden (ver PRIORIDAD_NORTE).
    También reordena una vuelta que ya estaba en curso, sin perder lo ya cubierto. Sin ella el orden
    es aleatorio, como siempre.
    """
    estado = _cargar(archivo)
    if not estado or not estado.get("pendientes"):
        estado = _nueva_vuelta(comunas, terminos, estado.get("vuelta", 0) if estado else 0)
        _guardar(archivo, estado)
        print(f"🔄 Iniciando vuelta de barrido exhaustivo N°{estado['vuelta']} "
              f"({len(estado['pendientes'])} combinaciones comuna+término).")
    else:
        conocidos = set(estado.get("terminos") or ({t for _, t in estado["pendientes"]} | set(terminos_previos)))
        nuevos = [t for t in terminos if t not in conocidos]
        if nuevos:
            extra = [[c, t] for c in comunas for t in nuevos]
            random.shuffle(extra)
            estado["pendientes"] = estado["pendientes"] + extra
            print(f"➕ Rubros nuevos en el barrido: {', '.join(nuevos)} ({len(extra)} combinaciones).")
        if nuevos or "terminos" not in estado:
            estado["terminos"] = list(terminos)
            _guardar(archivo, estado)

    if prioridad:
        ordenadas = _ordenar_por_prioridad(estado["pendientes"], prioridad)
        if ordenadas != estado["pendientes"]:
            estado["pendientes"] = ordenadas
            _guardar(archivo, estado)

    zona, termino = estado["pendientes"][0]
    print(f"📍 Combinación elegida (vuelta {estado['vuelta']}, quedan "
          f"{len(estado['pendientes'])} por cubrir): {termino} en {zona}")
    return zona, termino


def marcar_cubierto(archivo, zona, termino):
    """Saca la combinación de pendientes. Solo debe llamarse si la búsqueda efectivamente corrió."""
    estado = _cargar(archivo)
    if not estado or not estado.get("pendientes"):
        return
    if estado["pendientes"][0] == [zona, termino]:
        estado["pendientes"].pop(0)
        _guardar(archivo, estado)
