"""
Leads entregados al vendedor. Se guardan en un archivo aparte (asignaciones_vendedor.csv) y NO dentro
de los CSV de leads: los workers reescriben esos CSV cada hora y una edición manual se pisaría con ellos.

Los workers de clínicas y almacenes saltan estos leads (y no los reciclan), para que un prospecto no
reciba a la vez los mensajes automáticos y el contacto del vendedor.
"""
import logging
import os

import pandas as pd

ARCHIVO = "asignaciones_vendedor.csv"
COLUMNAS = ["Telefono", "Linea", "Nombre", "Asignado_A", "Fecha_Asignacion", "Semana"]


def clave_telefono(tel):
    """Últimos 9 dígitos: forma común para comparar teléfonos de distinto formato."""
    return "".join(filter(str.isdigit, str(tel)))[-9:]


def cargar(archivo=ARCHIVO):
    if not os.path.exists(archivo):
        return pd.DataFrame(columns=COLUMNAS)
    try:
        df = pd.read_csv(archivo, dtype=str).fillna("")
    except Exception:
        logging.exception("No se pudo leer %s; se ignoran las asignaciones.", archivo)
        return pd.DataFrame(columns=COLUMNAS)
    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = ""
    return df


def cargar_asignados(archivo=ARCHIVO):
    """Conjunto de claves de teléfono ya entregadas al vendedor (vacío si no hay archivo)."""
    return {clave_telefono(t) for t in cargar(archivo)["Telefono"] if clave_telefono(t)}


def registrar(filas, archivo=ARCHIVO):
    """
    Agrega asignaciones (lista de dicts con las columnas de COLUMNAS). No duplica teléfonos ya
    asignados. Devuelve la cantidad efectivamente agregada.
    """
    actual = cargar(archivo)
    ya = {clave_telefono(t) for t in actual["Telefono"]}
    nuevas = [f for f in filas if clave_telefono(f["Telefono"]) not in ya]
    if not nuevas:
        return 0
    total = pd.concat([actual, pd.DataFrame(nuevas)[COLUMNAS]], ignore_index=True)
    total.to_csv(archivo, index=False, encoding="utf-8")
    return len(nuevas)
