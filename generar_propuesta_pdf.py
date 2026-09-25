"""
Genera la propuesta comercial de GestionVital Pro en PDF (planes, precios y
promociones), lista para enviar por WhatsApp o correo.

Lee los datos EXCLUSIVAMENTE de playbook_ventas.yaml, la misma fuente de verdad
que usa el agente conversacional. Si cambias precios, planes o promociones ahi,
solo hay que volver a correr este script para actualizar el PDF: no hay nada
que editar a mano en este archivo.

Uso:
    python generar_propuesta_pdf.py

Genera: propuestas/GestionVital_Pro_propuesta.pdf (carpeta ignorada por git:
son datos comerciales para enviar, no para versionar).

Nota de codificacion: fpdf (la libreria que usamos) solo soporta caracteres
Latin-1 con las fuentes base -> nada de guiones largos ni comillas tipograficas
en los textos de este archivo. Tildes, enie, ¿ y ¡ si funcionan bien (se probo
por separado).
"""
import os

import yaml
from fpdf import FPDF

PLAYBOOK_PATH = "playbook_ventas.yaml"
CARPETA_SALIDA = "propuestas"
ARCHIVO_SALIDA = "GestionVital_Pro_propuesta.pdf"

# Colores oficiales: azul y verde del logo de GestionVital Pro (muestreados de
# "Logo 1.png"), y el azul marino del logo de ServiGod (identidad de la agencia
# que desarrolla el producto). Mismos tokens que usa propuesta_gestionvital.html.
AZUL = (0, 104, 201)       # #0068C9 - GestionVital (wordmark "Gestion Vital")
VERDE = (0, 131, 109)      # #00836D - GestionVital (acento "PRO")
NAVY = (20, 32, 46)        # #14202E - ServiGod (franjas: el logo se lee mejor aqui que sobre el azul)
BAND_INK = (255, 255, 255)
BAND_INK_SOFT = (176, 188, 204)
INK = NAVY
INK_SOFT = (82, 96, 116)
LINE = (221, 228, 237)
SURFACE2 = (234, 241, 248)
WHITE = (255, 255, 255)
LOGO_GESTIONVITAL = "Logo 1.png"
LOGO_SERVIGOD_BLANCO = "servigod_logo_blanco.png"

MARGIN = 15
PAGE_H = 297
CONTENT_W = 210 - MARGIN * 2  # 180 mm (A4)
PIE_PAGINA_H = 10  # espacio reservado abajo para no pisar el pie de pagina


def moneda(n):
    return "$" + f"{int(n):,}".replace(",", ".")


def cargar_playbook():
    with open(PLAYBOOK_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def plan_por_id(planes, pid):
    return next(p for p in planes if p["id"] == pid)


def ajustar_fuente(pdf, textos, ancho_max, tam_inicial, tam_min=6.5, estilo=""):
    """El tamano de fuente mas grande (hasta tam_min) con el que TODOS los textos
    entran en una linea de ancho ancho_max. Evita que un feature largo se corte."""
    tam = tam_inicial
    while tam > tam_min:
        pdf.set_font("Helvetica", estilo, tam)
        if all(pdf.get_string_width(t) <= ancho_max for t in textos):
            return tam
        tam -= 0.2
    pdf.set_font("Helvetica", estilo, tam_min)
    return tam_min


def lineas_de_multicell(pdf, texto, ancho, tam, estilo=""):
    """Cuantas lineas ocupara `texto` en un multi_cell de este ancho (para poder
    calcular alturas ANTES de dibujar, y que columnas paralelas queden alineadas)."""
    pdf.set_font("Helvetica", estilo, tam)
    palabras = texto.split(" ")
    lineas, actual = 1, ""
    for palabra in palabras:
        prueba = (actual + " " + palabra).strip()
        if pdf.get_string_width(prueba) <= ancho:
            actual = prueba
        else:
            lineas += 1
            actual = palabra
    return lineas


def bullet(pdf, x, y, texto, color_marca, tam, color_texto=None):
    """Un item con vinieta cuadrada (evita depender de glifos fuera de Latin-1). El texto usa
    color_texto si se indica, o color_marca si no (para cuando ambos deben ser el mismo, p. ej.
    texto blanco sobre una franja de color)."""
    pdf.set_fill_color(*color_marca)
    pdf.rect(x, y + 1.1, 1.6, 1.6, "F")
    pdf.set_xy(x + 3.2, y - 0.6)
    pdf.set_font("Helvetica", "", tam)
    pdf.set_text_color(*(color_texto or color_marca))
    pdf.cell(0, 4.2, texto)


def texto_tachado(pdf, x, y, texto, tam, color):
    pdf.set_font("Helvetica", "", tam)
    pdf.set_text_color(*color)
    pdf.set_xy(x, y)
    pdf.cell(pdf.get_string_width(texto) + 1, 5, texto)
    ancho = pdf.get_string_width(texto)
    pdf.set_draw_color(*color)
    pdf.set_line_width(0.35)
    pdf.line(x, y + 2.6, x + ancho, y + 2.6)


def kicker_titulo(pdf, y, kicker, titulo, subtitulo=None):
    pdf.set_xy(MARGIN, y)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*VERDE)
    pdf.cell(0, 4, kicker.upper())
    pdf.set_xy(MARGIN, y + 5)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(*INK)
    pdf.cell(0, 7, titulo)
    y2 = y + 13
    if subtitulo:
        pdf.set_xy(MARGIN, y2)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*INK_SOFT)
        pdf.multi_cell(CONTENT_W, 4.6, subtitulo)
        y2 = pdf.get_y() + 1
    return y2


def franja_banda(pdf, y, h, titulo, texto, extra=None):
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, y, 210, h, "F")
    pdf.set_xy(MARGIN, y + 8)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*BAND_INK)
    pdf.cell(0, 8, titulo)
    pdf.set_xy(MARGIN, y + 17)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*BAND_INK_SOFT)
    pdf.multi_cell(CONTENT_W, 4.8, texto)
    if extra:
        extra(pdf.get_y() + 3)


def construir_pdf(datos):
    empresa = datos.get("empresa", {})
    planes = datos.get("planes", [])
    promos = datos.get("promociones", [])
    demo = datos.get("demo", {})
    actualizado = datos.get("actualizado", "")
    trial_dias = empresa.get("trial_dias", 14)

    basico = plan_por_id(planes, "basico")
    medio = plan_por_id(planes, "medio")
    avanzado = plan_por_id(planes, "avanzado")
    ecommerce = plan_por_id(planes, "ecommerce_addon")
    combo = next((p for p in promos if p["id"] == "combo_basico_ecommerce"), None)
    avanzado_promo = next((p for p in promos if p["id"] == "avanzado_precio_medio"), None)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()

    # ---------- Encabezado ----------
    BAND_H = 58
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, BAND_H, "F")
    if os.path.exists(LOGO_GESTIONVITAL):
        logo_w = 58
        logo_h = logo_w * 160 / 440  # proporcion real del logo (440x160)
        pdf.image(LOGO_GESTIONVITAL, MARGIN, 9, w=logo_w, h=logo_h)
        ty = 9 + logo_h + 4
    else:
        ty = 12
    pdf.set_xy(MARGIN, ty)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*BAND_INK_SOFT)
    pdf.multi_cell(CONTENT_W, 4.2, "SOFTWARE DE GESTION PARA CENTROS ESTETICOS, PELUQUERIAS Y KINESIOLOGIA")
    pdf.set_xy(MARGIN, ty + 6)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*BAND_INK)
    pdf.multi_cell(
        CONTENT_W, 4.6,
        "Agenda, fichas de pacientes o clientes, inventario y ventas en un solo sistema, "
        "para el trabajo diario de centros esteticos, peluquerias y centros de kinesiologia en Chile.",
    )

    y = ty + 17
    chips = [
        f"{trial_dias} dias de prueba gratis",
        "Sin contrato de permanencia",
        "Migracion de tus datos incluida",
    ]
    x = MARGIN
    for c in chips:
        bullet(pdf, x, y, c, BAND_INK, 8.3)
        pdf.set_font("Helvetica", "", 8.3)
        x += pdf.get_string_width(c) + 12

    # ---------- Que resuelve ----------
    y = BAND_H + 10
    y = kicker_titulo(pdf, y, "Que resuelve", "Todo el negocio, en un solo sistema") + 3
    pillares = [
        ("Agenda y fichas", "Agenda de citas y fichas de pacientes o clientes, con evolucion y fotos del tratamiento."),
        ("Servicios y equipo", "Catalogo de servicios, gestion de profesionales y promociones."),
        ("Insumos y cobros", "Inventario de insumos, anulacion de cobros y carritos abandonados."),
        ("Reportes y fidelizacion", "Reportes, encuestas de satisfaccion y reporte de recompra."),
    ]
    col_w = (CONTENT_W - 6) / 2
    tam_pilar = 8.3
    lineas_por_fila = [
        max(lineas_de_multicell(pdf, pillares[i][1], col_w, tam_pilar), lineas_de_multicell(pdf, pillares[i + 1][1], col_w, tam_pilar))
        for i in (0, 2)
    ]
    fila_y = [y, y + 5 + lineas_por_fila[0] * 3.9 + 5]
    for i, (titulo, desc) in enumerate(pillares):
        fila = i // 2
        px = MARGIN + (i % 2) * (col_w + 6)
        py = fila_y[fila]
        pdf.set_xy(px, py)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.cell(col_w, 4.5, titulo)
        pdf.set_xy(px, py + 5)
        pdf.set_font("Helvetica", "", tam_pilar)
        pdf.set_text_color(*INK_SOFT)
        pdf.multi_cell(col_w, 3.9, desc)
    y = fila_y[1] + 5 + lineas_por_fila[1] * 3.9 + 4

    # ---------- Planes ----------
    y = kicker_titulo(
        pdf, y, "Planes", "Un plan para cada etapa",
        "Precios netos, se suma IVA. Ningun plan tiene contrato de permanencia.",
    ) + 1

    tarjetas = [
        {"plan": basico, "oferta": False},
        {"plan": medio, "oferta": False},
        {"plan": avanzado, "oferta": True},
    ]
    gap = 4
    card_w = (CONTENT_W - gap * 2) / 3
    pad = 4
    text_w = card_w - pad * 2 - 3.2

    todas_features = [f for t in tarjetas for f in t["plan"].get("incluye", [])]
    tam_feat = ajustar_fuente(pdf, todas_features, text_w, 8.2, 6.8)

    max_lineas = max(len(t["plan"].get("incluye", [])) for t in tarjetas)
    card_h = 23 + max_lineas * 4.6 + pad

    card_y = y + 4  # deja espacio arriba para la cinta "Oferta"
    for i, t in enumerate(tarjetas):
        plan = t["plan"]
        cx = MARGIN + i * (card_w + gap)
        borde = VERDE if t["oferta"] else LINE
        grosor = 0.7 if t["oferta"] else 0.3
        pdf.set_draw_color(*borde)
        pdf.set_line_width(grosor)
        pdf.set_fill_color(*WHITE)
        pdf.rect(cx, card_y, card_w, card_h, "DF")

        if t["oferta"]:
            pdf.set_fill_color(*VERDE)
            pdf.rect(cx + 4, card_y - 3.2, 17, 6, "F")
            pdf.set_xy(cx + 4, card_y - 3.2)
            pdf.set_font("Helvetica", "B", 7.3)
            pdf.set_text_color(*WHITE)
            pdf.cell(17, 6, "OFERTA", align="C")

        ty = card_y + pad
        pdf.set_xy(cx + pad, ty)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(*INK)
        pdf.cell(card_w - pad * 2, 5, plan["nombre"].split(" (")[0])
        ty += 7

        if t["oferta"] and plan.get("precio_lista_clp"):
            texto_tachado(pdf, cx + pad, ty, moneda(plan["precio_lista_clp"]), 9, INK_SOFT)
            ty += 5.5
        pdf.set_xy(cx + pad, ty)
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(*INK)
        precio_txt = moneda(plan["precio_mensual_clp"])
        pdf.cell(pdf.get_string_width(precio_txt) + 2, 6.5, precio_txt)
        pdf.set_font("Helvetica", "", 7.6)
        pdf.set_text_color(*INK_SOFT)
        pdf.cell(0, 6.5, " + IVA / mes")
        ty += 6.5

        if t["oferta"]:
            pdf.set_xy(cx + pad, ty)
            pdf.set_font("Helvetica", "B", 7.6)
            pdf.set_text_color(*VERDE)
            pdf.multi_cell(card_w - pad * 2, 3.6, "El plan completo, al precio del Plan Medio.")
            ty = pdf.get_y() + 1
        else:
            ty += 3

        for feat in plan.get("incluye", []):
            bullet(pdf, cx + pad, ty, feat, AZUL, tam_feat, color_texto=INK)
            ty += 4.6

    y = card_y + card_h + 6

    # ---------- Addon ----------
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.set_fill_color(*SURFACE2)
    addon_h = 19
    pdf.rect(MARGIN, y, CONTENT_W, addon_h, "DF")
    pdf.set_xy(MARGIN + pad, y + 4)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*INK)
    pdf.cell(0, 5, ecommerce["nombre"].split(" (")[0])
    pdf.set_xy(MARGIN + pad, y + 10)
    pdf.set_font("Helvetica", "B", 12.5)
    precio_txt = moneda(ecommerce["precio_mensual_clp"])
    pdf.cell(pdf.get_string_width(precio_txt) + 2, 6, precio_txt)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*INK_SOFT)
    pdf.cell(0, 6, " + IVA / mes  -  se suma a cualquier plan")
    lista_addon = "   ".join(f"- {f}" for f in ecommerce.get("incluye", []))
    pdf.set_xy(MARGIN + pad, y + 15.5)
    pdf.set_font("Helvetica", "", 7.6)
    pdf.set_text_color(*INK_SOFT)
    pdf.cell(0, 4, lista_addon)
    y += addon_h + 8

    # ---------- Promociones (en la pagina 1 si queda espacio, si no en la 2) ----------
    promo_gap = 6
    promo_w = (CONTENT_W - promo_gap) / 2
    promo_h = 26
    bloque_promos_h = 13 + promo_h
    if y + bloque_promos_h > PAGE_H - MARGIN - PIE_PAGINA_H:
        pdf.add_page()
        y = MARGIN

    y = kicker_titulo(pdf, y, "Promociones vigentes", "Dos formas de partir con mas por menos") + 3
    for i, promo in enumerate((combo, avanzado_promo)):
        if not promo:
            continue
        px = MARGIN + i * (promo_w + promo_gap)
        pdf.set_fill_color(*SURFACE2)
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.3)
        pdf.rect(px, y, promo_w, promo_h, "DF")
        pdf.set_xy(px + pad, y + 4)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(*INK)
        pdf.multi_cell(promo_w - pad * 2, 4.2, promo["nombre"])
        py = pdf.get_y() + 1
        pdf.set_xy(px + pad, py)
        pdf.set_font("Helvetica", "B", 12.5)
        pdf.set_text_color(*INK)
        precio_txt = moneda(promo["precio_mensual_clp"])
        pdf.cell(pdf.get_string_width(precio_txt) + 2, 6, precio_txt)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*INK_SOFT)
        pdf.cell(0, 6, " + IVA / mes")
    y += promo_h + 10

    # ---------- Garantias ----------
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN, y, MARGIN + CONTENT_W, y)
    y += 6
    garantias = [
        f"{trial_dias} dias de prueba gratis",
        "Sin contrato de permanencia",
        "Migracion de datos sin costo",
    ]
    gw = CONTENT_W / 3
    for i, g in enumerate(garantias):
        bullet(pdf, MARGIN + i * gw, y, g, AZUL, 8.5, color_texto=INK)
    y += 13

    # ---------- FAQ ----------
    faq = [
        ("Sirve para peluquerias y centros de kinesiologia, o solo para estetica?",
         "Sirve para los tres: agenda de citas, fichas de pacientes o clientes y gestion de "
         "profesionales funcionan igual para un centro estetico, una peluqueria o un centro de kinesiologia."),
        ("Tiene contrato de permanencia?",
         "No. Ningun plan tiene contrato de permanencia: cancelas cuando quieras."),
        ("Puedo probarlo antes de pagar?",
         f"Si. Tienes {trial_dias} dias de prueba gratis al registrarte, o te mostramos el sistema "
         "en una demo guiada por WhatsApp."),
        ("Ya administro todo por Excel o WhatsApp, vale la pena cambiar?",
         "Los recordatorios de WhatsApp y la agenda online se envian solos, asi que se pierden menos "
         "horas por inasistencias no avisadas. La migracion de tus datos actuales esta incluida sin costo."),
    ]
    faq_alturas = []
    for pregunta, respuesta in faq:
        lp = lineas_de_multicell(pdf, "?" + pregunta, CONTENT_W, 9.3, estilo="B")
        lr = lineas_de_multicell(pdf, respuesta, CONTENT_W, 8.6)
        faq_alturas.append(4 + lp * 4.3 + 1 + lr * 4 + 4)
    bloque_faq_h = 13 + sum(faq_alturas)
    if y + bloque_faq_h > PAGE_H - MARGIN - PIE_PAGINA_H - 40:
        pdf.add_page()
        y = MARGIN

    y = kicker_titulo(pdf, y, "Preguntas frecuentes", "Antes de decidir") + 2
    for (pregunta, respuesta), alto in zip(faq, faq_alturas):
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.25)
        pdf.line(MARGIN, y, MARGIN + CONTENT_W, y)
        y += 4
        pdf.set_xy(MARGIN, y)
        pdf.set_font("Helvetica", "B", 9.3)
        pdf.set_text_color(*INK)
        pdf.multi_cell(CONTENT_W, 4.3, "¿" + pregunta)
        y = pdf.get_y() + 1
        pdf.set_xy(MARGIN, y)
        pdf.set_font("Helvetica", "", 8.6)
        pdf.set_text_color(*INK_SOFT)
        pdf.multi_cell(CONTENT_W, 4, respuesta)
        y = pdf.get_y() + 4

    # ---------- Cierre / CTA ----------
    band_h = 56
    if y + band_h > PAGE_H - PIE_PAGINA_H:
        pdf.add_page()
        y = MARGIN

    def cta_extra(yy):
        pdf.set_xy(MARGIN, yy)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*BAND_INK)
        registro = empresa.get("registro_trial", "")
        pdf.cell(0, 5, f"Registrarme gratis: {registro}", link=registro if registro else "")
        pdf.set_xy(MARGIN, yy + 6)
        pdf.set_font("Helvetica", "", 8.6)
        pdf.set_text_color(*BAND_INK_SOFT)
        dias_demo = ", ".join(d.capitalize() for d in demo.get("dias_disponibles", []))
        pdf.cell(
            0, 4.5,
            f"Demo por WhatsApp: {dias_demo}, de {demo.get('horario_inicio', '')} a "
            f"{demo.get('horario_fin', '')}, {demo.get('duracion_minutos', '')} minutos.",
        )
        # Credito: GestionVital Pro es un producto de ServiGod.
        py = yy + 15
        pdf.set_draw_color(*BAND_INK_SOFT)
        pdf.set_line_width(0.2)
        pdf.line(MARGIN, py, MARGIN + CONTENT_W, py)
        pdf.set_xy(MARGIN, py + 3.5)
        pdf.set_font("Helvetica", "", 7.6)
        pdf.set_text_color(*BAND_INK_SOFT)
        pdf.cell(28, 5, "Un producto de")
        if os.path.exists(LOGO_SERVIGOD_BLANCO):
            sg_w = 26
            sg_h = sg_w * 350 / 1675  # proporcion real del logo (1675x350)
            pdf.image(LOGO_SERVIGOD_BLANCO, MARGIN + 26, py + 2, w=sg_w, h=sg_h)

    franja_banda(
        pdf, y, band_h, "¿Conversamos?",
        "Responde este mensaje por WhatsApp y coordinamos una demo guiada sin costo, o "
        "registrate directamente para tu prueba gratis.",
        extra=cta_extra,
    )
    y += band_h + 4

    pdf.set_xy(MARGIN, y)
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(*INK_SOFT)
    pdf.cell(0, 4, f"Precios en pesos chilenos (CLP), valores netos, se suma IVA. Actualizado el {actualizado}.")

    return pdf


def main():
    datos = cargar_playbook()
    pdf = construir_pdf(datos)
    os.makedirs(CARPETA_SALIDA, exist_ok=True)
    ruta = os.path.join(CARPETA_SALIDA, ARCHIVO_SALIDA)
    pdf.output(ruta)
    print(f"Generado: {os.path.abspath(ruta)}")
    print(f"Paginas: {pdf.page_no()}")
    print("Listo para adjuntar directo en WhatsApp o correo.")


if __name__ == "__main__":
    main()
