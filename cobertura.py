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


def siguiente(archivo, comunas, terminos, terminos_previos=()):
    """
    Devuelve (zona, termino) a buscar, sin marcarla como cubierta (eso lo hace
    marcar_cubierto una vez que la búsqueda corrió de verdad). `terminos_previos` son
    los rubros que ya existían antes de que el estado guardara su lista de rubros
    (para no confundirlos con rubros nuevos al migrar).
    Si se agregan rubros a `terminos` con una vuelta en curso, sus combinaciones se
    suman a las pendientes sin reiniciar la vuelta.
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
