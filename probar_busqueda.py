"""
Prueba SIN efectos de la búsqueda de leads: consulta SerpAPI de verdad, aplica todos los filtros y
muestra, negocio por negocio, qué se haría con cada uno.

No envía mensajes, no escribe los CSV ni el estado de cobertura/presupuesto. Solo gasta búsquedas
de SerpAPI (1 por página) y consulta a Evolution qué números tienen WhatsApp (solo lectura).

Uso:
    python probar_busqueda.py                                   # 2 consultas de ejemplo, 1 página c/u
    python probar_busqueda.py --linea almacenes --comuna Independencia --termino Almacen
    python probar_busqueda.py --linea clinicas --comuna Ñuñoa --termino "Centro de Belleza" --paginas 2

Lee SERP_KEY de .env.serp y EVO_URL/EVO_TOKEN/EVO_INSTANCE de .env.v2 (o del entorno).
"""
import argparse
import os
import sys

import captacion
import filtros_leads
import serp_client
import web_scraper
from evo_client import es_movil_chileno, normalizar_telefono_chile

CONSULTAS_EJEMPLO = [
    ("almacenes", "Independencia", "Almacen"),   # el caso que trajo telas y bodegas
    ("almacenes", "Ñuñoa", "Minimarket"),        # un caso "normal"
]


def cargar_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def diagnostico(place, linea):
    """Motivo por el que un negocio se descarta antes de verificar WhatsApp, o None si pasa."""
    if filtros_leads.es_cadena(place.get("title", ""), linea):
        return "CADENA"
    if not filtros_leads.categoria_relevante(place, linea):
        return "FUERA DE RUBRO"
    tel = normalizar_telefono_chile(place.get("phone", ""))
    if es_movil_chileno(tel):
        return None
    if place.get("website"):
        return None  # se intentará rescatar un celular desde su web
    return "SIN CELULAR NI WEB"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--linea", choices=["almacenes", "clinicas"])
    ap.add_argument("--comuna")
    ap.add_argument("--termino")
    ap.add_argument("--paginas", type=int, default=1, help="páginas de resultados por consulta (1 búsqueda c/u)")
    args = ap.parse_args()

    cargar_env(".env.serp")
    cargar_env(".env.v2")
    key = os.getenv("SERP_KEY")
    if not key:
        sys.exit("Falta SERP_KEY (ponla en .env.serp).")
    evo = (os.getenv("EVO_URL"), os.getenv("EVO_TOKEN"), os.getenv("EVO_INSTANCE"))

    consultas = [(args.linea, args.comuna, args.termino)] if args.linea and args.comuna and args.termino else CONSULTAS_EJEMPLO
    for linea, comuna, termino in consultas:
        print("\n" + "=" * 100)
        print(f"{linea.upper()} — '{termino}' en {comuna}  ({args.paginas} página(s))")
        print("=" * 100)
        resultados = []
        for n in range(args.paginas):
            estado, res = serp_client.buscar_pagina(f"{termino} {comuna} Chile", key, start=n * serp_client.PAGINA_TAMANO)
            if estado != "ok":
                print(f"   (página {n + 1}: {estado})")
                break
            resultados += res
        print(f"\n{len(resultados)} negocios devueltos por Google Maps:\n")

        print(f"{'NEGOCIO':44} {'CATEGORÍA (Google)':30} {'TEL':6} {'WEB':4} DECISIÓN")
        for p in resultados:
            tel = normalizar_telefono_chile(p.get("phone", ""))
            tipo_tel = "móvil" if es_movil_chileno(tel) else ("fijo" if tel else "—")
            motivo = diagnostico(p, linea)
            print(f"{str(p.get('title', '?'))[:43]:44} {filtros_leads.texto_categoria(p)[:29] or '(sin categoría)':30} "
                  f"{tipo_tel:6} {'sí' if p.get('website') else 'no':4} {motivo or 'candidato'}")

        descartes = {"sin_movil": 0, "cadena": 0, "duplicado": 0, "sin_whatsapp": 0}
        candidatos = captacion.preparar_candidatos(
            resultados, set(), linea, descartes, necesita_email=(linea == "clinicas")
        )
        confirmados = captacion.confirmar_con_whatsapp(candidatos, *evo, descartes)
        print(f"\nDescartados: {descartes}")
        print(f"\n➡️  LEADS QUE SE AGREGARÍAN: {len(confirmados)}")
        for cand, tel in confirmados:
            origen = "celular sacado de su web" if cand["origen"] == "web" else "celular de Google"
            print(f"   • {cand['place'].get('title')}  →  {tel}  ({origen})")
    print("\nNo se envió ningún mensaje ni se modificó ningún archivo de datos.")


if __name__ == "__main__":
    main()
