"""
Monitor de licitaciones en Mercado Publico (ChileCompra).

Usa la API publica oficial: https://api.mercadopublico.cl/
Requiere un "ticket" (API key) gratuito, obtenido en https://api.mercadopublico.cl/

Uso:
    set MERCADOPUBLICO_TICKET=tu-ticket-aqui   (Windows cmd)
    $env:MERCADOPUBLICO_TICKET="tu-ticket-aqui"   (PowerShell)

    python mercadopublico_monitor.py --dias 3 --keywords "consultoria,capacitacion,asesoria"
"""

import argparse
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta

import pandas as pd
import requests

API_BASE = "https://api.mercadopublico.cl/servicios/v1/publico"
ARCHIVO_SALIDA = "licitaciones_mercadopublico.csv"
ARCHIVO_VISTAS = "licitaciones_vistas.csv"


def _normalizar(texto):
    if not isinstance(texto, str):
        return ""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.lower()


def obtener_licitaciones_del_dia(fecha, ticket, reintentos=3):
    """Consulta licitaciones publicadas en una fecha dada (dd-mm-yyyy)."""
    url = f"{API_BASE}/licitaciones.json"
    params = {"fecha": fecha.strftime("%d%m%Y"), "ticket": ticket}

    for intento in range(1, reintentos + 1):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("Listado", [])
        except requests.RequestException as exc:
            print(f"  [!] Intento {intento}/{reintentos} fallo para {fecha.date()}: {exc}")
            time.sleep(2 * intento)
    return []


def filtrar_por_keywords(licitaciones, keywords):
    if not keywords:
        return licitaciones
    keywords_norm = [_normalizar(k.strip()) for k in keywords if k.strip()]
    filtradas = []
    for lic in licitaciones:
        texto = _normalizar(lic.get("Nombre", "") + " " + lic.get("CodigoEstado", "") + " " + str(lic.get("Descripcion", "")))
        if any(kw in texto for kw in keywords_norm):
            filtradas.append(lic)
    return filtradas


def cargar_vistas():
    if os.path.exists(ARCHIVO_VISTAS):
        return set(pd.read_csv(ARCHIVO_VISTAS)["CodigoExterno"].astype(str))
    return set()


def guardar_vistas(codigos_nuevos, ya_vistas):
    todas = ya_vistas | set(codigos_nuevos)
    pd.DataFrame({"CodigoExterno": sorted(todas)}).to_csv(ARCHIVO_VISTAS, index=False)


def main():
    parser = argparse.ArgumentParser(description="Monitor de licitaciones Mercado Publico")
    parser.add_argument("--dias", type=int, default=3, help="Cuantos dias hacia atras revisar (default 3)")
    parser.add_argument(
        "--keywords",
        type=str,
        default="",
        help="Palabras clave separadas por coma para filtrar (ej: 'consultoria,capacitacion')",
    )
    parser.add_argument("--ticket", type=str, default=None, help="Ticket API (o usa variable de entorno MERCADOPUBLICO_TICKET)")
    args = parser.parse_args()

    ticket = args.ticket or os.getenv("MERCADOPUBLICO_TICKET")
    if not ticket:
        print("ERROR: falta el ticket de la API.")
        print("Consiguelo gratis en https://api.mercadopublico.cl/ y pasalo con --ticket o la variable MERCADOPUBLICO_TICKET")
        sys.exit(1)

    keywords = [k for k in args.keywords.split(",") if k.strip()]
    print(f"Buscando licitaciones de los ultimos {args.dias} dia(s)...")
    if keywords:
        print(f"Filtro de palabras clave: {keywords}")

    todas = []
    hoy = datetime.now()
    for i in range(args.dias):
        fecha = hoy - timedelta(days=i)
        print(f" - Consultando {fecha.strftime('%d-%m-%Y')}...")
        licitaciones = obtener_licitaciones_del_dia(fecha, ticket)
        print(f"   {len(licitaciones)} licitaciones publicadas ese dia")
        todas.extend(licitaciones)
        time.sleep(1)  # evitar saturar la API

    if not todas:
        print("No se encontraron licitaciones en el rango de fechas.")
        return

    filtradas = filtrar_por_keywords(todas, keywords)
    print(f"\n{len(filtradas)} licitaciones coinciden con tus palabras clave (de {len(todas)} totales)")

    if not filtradas:
        return

    df = pd.DataFrame(filtradas)
    columnas_utiles = [c for c in ["CodigoExterno", "Nombre", "CodigoEstado", "FechaCierre", "Organismo", "RegionUnidad"] if c in df.columns]
    df = df[columnas_utiles] if columnas_utiles else df

    ya_vistas = cargar_vistas()
    codigo_col = "CodigoExterno" if "CodigoExterno" in df.columns else None
    if codigo_col:
        nuevas = df[~df[codigo_col].astype(str).isin(ya_vistas)]
    else:
        nuevas = df

    df.to_csv(ARCHIVO_SALIDA, index=False, encoding="utf-8-sig")
    print(f"\nGuardado listado completo en: {ARCHIVO_SALIDA}")

    if not nuevas.empty:
        print(f"\n*** {len(nuevas)} licitaciones NUEVAS (no vistas antes) ***")
        for _, row in nuevas.iterrows():
            print(f" - [{row.get('CodigoExterno', '')}] {row.get('Nombre', '')} | cierra: {row.get('FechaCierre', '')}")
        if codigo_col:
            guardar_vistas(nuevas[codigo_col].astype(str).tolist(), ya_vistas)
    else:
        print("\nNo hay licitaciones nuevas desde la ultima corrida.")


if __name__ == "__main__":
    main()
