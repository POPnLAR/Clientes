"""
Prepara la entrega semanal de leads de CLÍNICAS para el vendedor, que trabaja SOLO por teléfono.
Genera un Excel con dos hojas:

  1. "Contactados sin respuesta": recibieron la secuencia automática completa y no respondieron.
  2. "Nunca contactados": todavía no se les ha escrito por WhatsApp (incluye teléfonos fijos, que la
     secuencia automática no puede alcanzar pero un vendedor sí puede llamar).

Funciona en DOS pasos, para no equivocarse:

    python entregar_semana.py --cantidad 30 --cantidad-nunca 20              # 1) VISTA PREVIA: no cambia nada
    python entregar_semana.py --cantidad 30 --cantidad-nunca 20 --confirmar --vendedor "Nombre"   # 2) registra

La vista previa muestra qué se entregaría y por qué se excluyó el resto, y genera un Excel de prueba.
Con --confirmar se genera el Excel definitivo y se registran los leads en asignaciones_vendedor.csv, para
que los workers automáticos dejen de escribirles. (Usa --cantidad-nunca 0 para entregar solo el grupo 1.)

Por seguridad se consulta al agente (VPS) qué leads ya respondieron; sin esa consulta no se entrega
nada, salvo que se indique --sin-agente (solo para pruebas). Las credenciales van en .env.entrega:
    AGENT_SERVICE_URL=https://gestionvitalpro.cl/agente-api
    AGENT_SERVICE_TOKEN=...
"""
import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import unicodedata

import pandas as pd

import agent_client
import asignaciones
import filtros_leads
from evo_client import es_movil_chileno, normalizar_telefono_chile

ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"
CARPETA = "entregas"
LINEA = "clinicas"
RUBROS_PREFERIDOS = {"Clinica Estetica", "Medicina Estetica"}
ESTADOS_VENDEDOR = "Sin llamar,No contesta,Llamar de nuevo,Interesado,Demo agendada,No interesado,Número incorrecto"
GRUPOS = {
    "sin_respuesta": "Contactados sin respuesta",
    "nunca": "Nunca contactados",
}

# El nombre debe sugerir estética/salud; si no, va a revisión manual en vez de al vendedor.
_RE_RUBRO_OK = re.compile(
    r"estetic|belleza|beauty|\bspa\b|cosmet|depila|laser|\bpiel\b|skin|facial|corporal|masaje|micropigment|"
    r"cejas|pestan|lash|clinic|medic|dermat|salon|nail|hands|feet|wellness|bella|studio|estudio|centro|"
    r"\bdra?\.|doctor|kine|glow|brow|hair|pelu|manicur|pedicur|podolog|estilo|salud|bienestar|rejuven|"
    r"lifting|botox|relleno|slim|body|cuerpo|unas|uñas|maquill|makeup|spa",
    re.IGNORECASE,
)
# Negocios de otro rubro que suelen colarse en las búsquedas (p. ej. "Clínica Veterinaria").
_RE_OTRO_RUBRO = re.compile(
    r"veterin|mascota|optic|farmac|inmobil|hotel|ferreter|restaurant|panader|supermerc|abogad|contab|mecanic",
    re.IGNORECASE,
)


# Códigos de área reales de teléfonos fijos en Chile (además del 2 de Santiago). Un fijo con otro código
# es un número mal capturado y no vale la pena llamarlo.
_CODIGOS_AREA = {
    "32", "33", "34", "35", "41", "42", "43", "45", "51", "52", "53", "55", "57", "58",
    "61", "63", "64", "65", "67", "71", "72", "73", "75",
}


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t)) if unicodedata.category(c) != "Mn").lower()


def limpiar_nombre(nombre):
    """Espacios normalizados, sin colas de SEO, largo acotado; TODO EN MAYÚSCULAS pasa a formato título."""
    n = re.sub(r"\s+", " ", str(nombre or "")).strip()
    n = n.split("|")[0].strip() or n          # "Marca | servicio | servicio ..." -> "Marca"
    if len(n) > 60:
        n = n[:60].rsplit(" ", 1)[0].rstrip(" ,-—") + "…"
    return n.title() if len(n) > 4 and n.isupper() else n


def tipo_telefono(tel):
    """'Celular', 'Fijo' o '' (600/800, incompleto o vacío: no sirve para llamar)."""
    d = "".join(filter(str.isdigit, normalizar_telefono_chile(tel)))
    if es_movil_chileno(d):
        return "Celular"
    if len(d) == 11 and d.startswith("56") and (d[2] == "2" or d[2:4] in _CODIGOS_AREA):
        return "Fijo"
    return ""


def formatear_telefono(tel):
    d = "".join(filter(str.isdigit, normalizar_telefono_chile(tel)))
    tipo = tipo_telefono(tel)
    if tipo == "Celular":
        return f"+56 9 {d[3:7]} {d[7:]}"
    if tipo == "Fijo":
        return f"+56 2 {d[3:7]} {d[7:]}" if d[2] == "2" else f"+56 {d[2:4]} {d[4:7]} {d[7:]}"
    return str(tel)


def rubro_de(ministerio):
    return str(ministerio or "").split(" - ", 1)[-1].strip() or "Estética"


def cargar_env(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _preparar(df):
    df = df.copy().fillna("")
    df["_clave"] = df["Telefono"].map(asignaciones.clave_telefono)
    df["_tel"] = df["Telefono"].map(normalizar_telefono_chile)
    df["_tipo"] = df["Telefono"].map(tipo_telefono)
    df["_ultimo"] = pd.to_datetime(df["Fecha_Contacto"], format="%d/%m/%Y %H:%M", errors="coerce")
    df["_alta"] = pd.to_datetime(df["Fecha"], format="%d/%m/%Y", errors="coerce")
    df["_enviados"] = pd.to_numeric(df["Dia_Secuencia"], errors="coerce").fillna(0).astype(int)
    return df


def seleccionar(df, respondieron, asignados, hoy, grupo="sin_respuesta", min_dias=3):
    """
    Devuelve (candidatos_df, motivos, revisar_df) para el grupo pedido.
      sin_respuesta: recibió la secuencia completa (Estado Finalizado), con celular, hace >= min_dias.
      nunca:         no recibió ningún mensaje automático (Estado Nuevo/Error sin envíos), con celular o fijo.
    En ambos: sin resultado registrado, sin respuesta, fuera de cadenas, de otro rubro y de asignaciones.
    `motivos` cuenta por qué se excluyó el resto.
    """
    motivos = {}

    def excluir(mask, motivo):
        nonlocal df
        n = int(mask.sum())
        if n:
            motivos[motivo] = motivos.get(motivo, 0) + n
        df = df[~mask]

    df = _preparar(df)

    if grupo == "sin_respuesta":
        excluir(df["Estado"] != "Finalizado", "no terminó la secuencia (Nuevo/Contactado/Rechazado/Error/Agendado)")
    else:
        excluir(df["_enviados"] > 0, "ya recibió mensajes automáticos")
        excluir(~df["Estado"].isin(["Nuevo", "Error"]), "estado que no corresponde (Rechazado, Agendado...)")

    excluir(df["Resultado"].str.strip() != "", "ya tiene un resultado registrado (no interesado, agenda, otro rubro...)")
    excluir(df["Notas"].str.contains("Respondi", case=False), "respondió por WhatsApp")
    if grupo == "sin_respuesta":
        excluir(df["_tipo"] != "Celular", "sin celular")
    else:
        excluir(df["_tipo"] == "", "teléfono que no sirve para llamar (600/800 o incompleto)")
    excluir(df["Evento"].map(lambda x: filtros_leads.es_cadena(x, LINEA)), "cadena o institución")
    excluir(df["_clave"].isin(asignados), "ya entregado antes al vendedor")
    excluir(df["_clave"].isin(respondieron), "ya respondió al agente")
    if grupo == "sin_respuesta":
        excluir(df["_ultimo"].isna() | ((hoy - df["_ultimo"]).dt.days < min_dias),
                f"último mensaje hace menos de {min_dias} días")
    excluir(df["Evento"].map(lambda x: bool(_RE_OTRO_RUBRO.search(_sin_acentos(x)))),
            "otro rubro (veterinaria, óptica, farmacia...)")

    dudoso = ~df["Evento"].map(lambda x: bool(_RE_RUBRO_OK.search(_sin_acentos(x))))
    revisar = df[dudoso].copy()
    if len(revisar):
        motivos["nombre que no parece de estética (para revisar)"] = len(revisar)
    df = df[~dudoso].copy()

    df["_pref"] = df["Ministerio"].map(rubro_de).isin(RUBROS_PREFERIDOS)
    df["_fijo"] = df["_tipo"] == "Fijo"
    if grupo == "sin_respuesta":
        df = df.sort_values(["_pref", "_ultimo"], ascending=[False, False])
    else:
        # Los fijos primero: el WhatsApp automático nunca podrá alcanzarlos, así que es donde el vendedor
        # más aporta. Los celulares "Nuevo" los alcanzaría la secuencia automática sin costo.
        df = df.sort_values(["_fijo", "_pref", "_alta"], ascending=[False, False, False])
    return df, motivos, revisar


def construir_filas(sel, grupo="sin_respuesta"):
    filas = []
    for i, (_, r) in enumerate(sel.iterrows(), start=1):
        if grupo == "sin_respuesta":
            ultimo = r["_ultimo"].strftime("%d/%m/%Y")
            contexto = f"Ya recibió {r['_enviados']} mensajes de WhatsApp de GestiónVital (el último el {ultimo}) y no respondió."
        else:
            ultimo = "—"
            contexto = "Todavía no se le ha escrito por WhatsApp: es el primer contacto."
            if r["_tipo"] == "Fijo":
                contexto += " Teléfono fijo: llama en horario de oficina."
        filas.append({
            "N°": i,
            "Negocio": limpiar_nombre(r["Evento"]),
            "Rubro": rubro_de(r["Ministerio"]),
            "Zona de búsqueda": str(r["Ubicacion"]).strip(),
            "Teléfono": formatear_telefono(r["Telefono"]),
            "Tipo": r["_tipo"],
            "Último WhatsApp enviado": ultimo,
            "Contexto": contexto,
            "Estado": "Sin llamar",
            "Fecha de llamada": "",
            "Comentarios": "",
            "_telefono_crudo": r["Telefono"],
        })
    return filas


def escribir_excel(hojas, ruta, vendedor, hoy):
    """hojas: {grupo: filas}. Una hoja de leads por grupo y una de instrucciones."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    wb.remove(wb.active)
    relleno = PatternFill("solid", fgColor="1F3A5F")
    anchos = {"N°": 5, "Negocio": 36, "Rubro": 20, "Zona de búsqueda": 18, "Teléfono": 18, "Tipo": 9,
              "Último WhatsApp enviado": 16, "Contexto": 54, "Estado": 20, "Fecha de llamada": 16, "Comentarios": 46}

    for grupo, filas in hojas.items():
        if not filas:
            continue
        columnas = [c for c in filas[0].keys() if not c.startswith("_")]
        if grupo == "nunca":
            columnas.remove("Último WhatsApp enviado")      # no aplica: no se les ha escrito
        ws = wb.create_sheet(GRUPOS[grupo])
        ws.append(columnas)
        for f in filas:
            ws.append([f[c] for c in columnas])
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = relleno
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, nombre in enumerate(columnas, start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = anchos.get(nombre, 16)
            for celda in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                celda[0].alignment = Alignment(vertical="top", wrap_text=nombre in ("Contexto", "Comentarios", "Negocio"))
        col_tel = columnas.index("Teléfono") + 1
        for fila in range(2, len(filas) + 2):
            ws.cell(fila, col_tel).font = Font(bold=True, size=12)   # el dato principal: se lee de un vistazo
        dv = DataValidation(type="list", formula1=f'"{ESTADOS_VENDEDOR}"', allow_blank=True)
        ws.add_data_validation(dv)
        col_estado = columnas.index("Estado") + 1
        dv.add(f"{ws.cell(2, col_estado).coordinate}:{ws.cell(len(filas) + 1, col_estado).coordinate}")
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions
        ws.row_dimensions[1].height = 30

    info = wb.create_sheet("Cómo usar")
    texto = [
        f"Entrega del {hoy.strftime('%d/%m/%Y')} — vendedor: {vendedor}",
        "",
        "Qué hay en cada hoja:",
        "  · Contactados sin respuesta: negocios de estética de la Región Metropolitana que ya recibieron mensajes automáticos",
        "    de GestiónVital Pro por WhatsApp y NO han respondido. Puedes mencionarlo al llamar: ya conocen el nombre.",
        "  · Nunca contactados: negocios a los que todavía no se les ha escrito; tu llamada es el primer contacto. Incluye",
        "    teléfonos fijos (columna Tipo): llámalos en horario de oficina.",
        "",
        "Orden de cada lista:",
        "  'Contactados sin respuesta': primero clínicas y medicina estética, y dentro de cada grupo los que recibieron el",
        "  último mensaje más recientemente. 'Nunca contactados': primero los teléfonos fijos, luego clínicas y medicina",
        "  estética, y dentro de cada grupo los que se agregaron más recientemente a la base.",
        "",
        "Cómo trabajarla (todo por teléfono):",
        "  1. Llama a cada negocio, en el orden de la lista.",
        "  2. Anota el resultado en la columna Estado (lista desplegable), la fecha de la llamada y los comentarios.",
        "  3. Si no contesta, márcalo 'No contesta' y vuelve a intentar otro día u horario ('Llamar de nuevo').",
        "  4. Si dice que no le interesa, márcalo 'No interesado' y no insistas.",
        "  5. Si le interesa, márcalo 'Interesado' o 'Demo agendada' y deja el detalle en Comentarios.",
        "  6. Devuelve el archivo actualizado al final de la semana.",
        "",
        "No llames a negocios que no estén en tu lista: pueden estar siendo atendidos por otra vía.",
        "",
        "Ojo con la 'Zona de búsqueda': es la comuna en la que se buscó el negocio, NO su dirección exacta (Google a veces",
        "devuelve negocios de zonas cercanas). Confirma la ubicación al hablar con ellos.",
    ]
    for t in texto:
        info.append([t])
    info.column_dimensions["A"].width = 130
    info["A1"].font = Font(bold=True, size=13)
    wb.save(ruta)


def auditar(df):
    """Leads de TODA la base (cualquier estado) cuyo nombre es de otro rubro o no sugiere estética."""
    nombres = df["Evento"].map(_sin_acentos)
    otro = nombres.map(lambda x: bool(_RE_OTRO_RUBRO.search(x)))
    dudoso = ~nombres.map(lambda x: bool(_RE_RUBRO_OK.search(x)))
    out = df[otro | dudoso].copy()
    out["_motivo"] = ["OTRO RUBRO" if o else "nombre sin pista de estética" for o in otro[out.index]]
    return out


def base_al_dia():
    """Avisa si la copia local está atrasada respecto de GitHub (los workers actualizan el CSV cada hora)."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin"], capture_output=True, timeout=30)
        n = subprocess.run(["git", "rev-list", "--count", "HEAD..origin/main"], capture_output=True, text=True, timeout=30)
        return int(n.stdout.strip() or 0) == 0
    except Exception:
        return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cantidad", type=int, default=30, help="cuántos 'contactados sin respuesta' entregar (por defecto 30)")
    ap.add_argument("--cantidad-nunca", type=int, default=20, help="cuántos 'nunca contactados' entregar (por defecto 20; 0 = ninguno)")
    ap.add_argument("--solo-fijos", action="store_true",
                    help="en 'nunca contactados' entrega solo teléfonos fijos (los que el WhatsApp automático no alcanza)")
    ap.add_argument("--vendedor", default="Vendedor", help="nombre del vendedor (queda registrado)")
    ap.add_argument("--min-dias", type=int, default=3, help="días mínimos desde el último mensaje automático")
    ap.add_argument("--confirmar", action="store_true", help="registra la entrega (sin esto es solo vista previa)")
    ap.add_argument("--sin-agente", action="store_true", help="NO verificar respuestas en el agente (solo pruebas)")
    ap.add_argument("--env", default=".env.entrega")
    ap.add_argument("--auditar", action="store_true",
                    help="lista los leads de otro rubro o de nombre dudoso en TODA la base (no entrega nada)")
    args = ap.parse_args()

    if args.auditar:
        base = pd.read_csv(ARCHIVO_LEADS, dtype=str).fillna("")
        a = auditar(base)
        print(f"Leads de clínicas de otro rubro o de nombre dudoso: {len(a)} de {len(base)}\n")
        print("⚠️  Los que están en Nuevo o Contactado SIGUEN recibiendo mensajes automáticos.")
        print("    Para descartarlos: dashboard > Editor de Base > Estado = Rechazado.\n")
        print(f"{'Id':>4} {'Estado':11} {'Negocio':46} {'Zona':15} Motivo")
        orden = {"Nuevo": 0, "Contactado": 1}
        for _, r in a.sort_values("Estado", key=lambda c: c.map(lambda x: orden.get(x, 2))).iterrows():
            aviso = "  <-- ACTIVO" if r["Estado"] in orden else ""
            print(f"{r['Id']:>4} {r['Estado']:11} {r['Evento'][:45]:46} {r['Ubicacion'][:14]:15} {r['_motivo']}{aviso}")
        return

    cargar_env(args.env)
    if not base_al_dia():
        print("⚠️  Tu copia local está ATRASADA respecto de GitHub. Ejecuta `git pull` y vuelve a correr esto: "
              "los workers actualizan los leads cada hora.\n")

    if args.sin_agente:
        respondieron = set()
        print("⚠️  Sin verificar respuestas en el agente (--sin-agente): NO uses este resultado para entregar.\n")
    else:
        respondieron, estado = agent_client.obtener_telefonos_que_respondieron()
        if estado != "ok":
            sys.exit(f"No se pudo consultar al agente (estado: {estado}). Revisa AGENT_SERVICE_URL y "
                     f"AGENT_SERVICE_TOKEN en {args.env}. Sin saber quién ya respondió no se entrega nada.")
        print(f"Agente consultado: {len(respondieron)} contactos ya respondieron y se excluyen de la entrega.\n")

    df = pd.read_csv(ARCHIVO_LEADS, dtype=str)
    hoy = pd.Timestamp(dt.datetime.now())
    asignados = asignaciones.cargar_asignados()
    print(f"Base de clínicas: {len(df)} leads")

    cantidades = {"sin_respuesta": args.cantidad, "nunca": args.cantidad_nunca}
    hojas, revisar_total = {}, {}
    ya_elegidos = set()          # un mismo lead no puede ir en los dos grupos
    for grupo, cantidad in cantidades.items():
        if cantidad <= 0:
            continue
        sel, motivos, revisar = seleccionar(df, respondieron, asignados | ya_elegidos, hoy, grupo, args.min_dias)
        if grupo == "nunca" and args.solo_fijos:
            motivos["celular (con --solo-fijos)"] = int((sel["_tipo"] != "Fijo").sum())
            sel = sel[sel["_tipo"] == "Fijo"]
        print(f"\n── {GRUPOS[grupo]} ──")
        for m, n in sorted(motivos.items(), key=lambda x: -x[1]):
            print(f"  - excluidos {n:3}  {m}")
        extra = f" (fijos: {int((sel['_tipo'] == 'Fijo').sum())}, celulares: {int((sel['_tipo'] == 'Celular').sum())})" if grupo == "nunca" else ""
        print(f"  = disponibles: {len(sel)}{extra}")
        for _, rr in revisar.iterrows():
            revisar_total[rr["Id"]] = rr
        elegidos = sel.head(cantidad)
        ya_elegidos |= set(elegidos["_clave"])
        hojas[grupo] = construir_filas(elegidos, grupo)

    if revisar_total:
        print("\nNombres que NO parecen de estética (revísalos, no van al vendedor):")
        for rr in revisar_total.values():
            print(f"    · {rr['Evento'][:45]:45} {rr['Ubicacion']}  ({rr['Telefono']}, {rr['Estado']})")

    total = sum(len(f) for f in hojas.values())
    if total == 0:
        sys.exit("\nNo hay leads disponibles para entregar con estas reglas.")
    print(f"\nEntrega propuesta: {total} leads")
    for grupo, filas in hojas.items():
        print(f"  {GRUPOS[grupo]}: {len(filas)}" + (f"  (fijos: {sum(f['Tipo'] == 'Fijo' for f in filas)})" if grupo == "nunca" else ""))
        for f in filas[:5]:
            print(f"      {f['N°']:2} {f['Negocio'][:36]:36} {f['Zona de búsqueda'][:14]:14} {f['Teléfono']:18} {f['Tipo']:7} {f['Rubro']}")
        if len(filas) > 5:
            print(f"      ... y {len(filas) - 5} más")

    os.makedirs(CARPETA, exist_ok=True)
    hoy_txt = hoy.strftime("%Y-%m-%d")
    if not args.confirmar:
        ruta = os.path.join(CARPETA, f"VISTA_PREVIA_{hoy_txt}.xlsx")
        escribir_excel(hojas, ruta, args.vendedor, hoy)
        print(f"\nVISTA PREVIA guardada en {ruta} (no se registró ninguna entrega).")
        print("Si está bien, repite el comando agregando:  --confirmar --vendedor \"Nombre\"")
        return

    nombre_arch = re.sub(r"[^A-Za-z0-9_-]+", "_", _sin_acentos(args.vendedor)).strip("_") or "vendedor"
    ruta = os.path.join(CARPETA, f"entrega_{hoy_txt}_{nombre_arch}.xlsx")
    escribir_excel(hojas, ruta, args.vendedor, hoy)
    semana = hoy.strftime("%G-W%V")
    n = asignaciones.registrar([
        {"Telefono": f["_telefono_crudo"], "Linea": LINEA, "Nombre": f["Negocio"], "Asignado_A": args.vendedor,
         "Fecha_Asignacion": hoy_txt, "Semana": semana, "Grupo": grupo}
        for grupo, filas in hojas.items() for f in filas
    ])
    print(f"\n✅ Entrega registrada: {n} leads asignados a {args.vendedor}.")
    print(f"   Excel para enviar: {ruta}")
    print("\nÚLTIMO PASO (para que los workers dejen de escribirles a estos leads):")
    print("   git checkout main && git pull")
    print("   git add asignaciones_vendedor.csv")
    print(f"   git commit -m \"Asignacion semanal al vendedor ({semana})\"")
    print("   git push origin main")


if __name__ == "__main__":
    main()
