"""
Genera la propuesta comercial de GestionAlmacen Pro en PDF (plan, precio y datos
confirmados del producto), lista para enviar por WhatsApp o correo.

Lee los datos EXCLUSIVAMENTE de playbook_almacenes.yaml, la misma fuente de verdad
que usa el agente conversacional. Si cambias el precio o los datos ahi, solo hay
que volver a correr este script para actualizar el PDF: no hay nada que editar a
mano en este archivo. Los campos de la seccion `no_disponible` del playbook (lo
que la pagina oficial no confirma) NUNCA se imprimen: son una instruccion interna
para que el agente no invente, no contenido para mostrarle a un prospecto.

Uso:
    python generar_propuesta_almacenes_pdf.py

Genera: propuestas/GestionAlmacen_Pro_propuesta.pdf (carpeta ignorada por git).

Nota de codificacion: fpdf solo soporta caracteres Latin-1 con las fuentes base ->
nada de guiones largos ni comillas tipograficas. Tildes, enie, ¿ y ¡ si funcionan.
"""
import os

import yaml
from fpdf import FPDF

PLAYBOOK_PATH = "playbook_almacenes.yaml"
CARPETA_SALIDA = "propuestas"
ARCHIVO_SALIDA = "GestionAlmacen_Pro_propuesta.pdf"

# Colores oficiales: azul primario y celeste del sitio/logo de GestionAlmacen Pro
# (gestionalmacenpro.cl usa Tailwind blue-600 / sky-400 en su propia web), y el
# slate-900 del mismo sitio para la franja (el logo, con su detalle en gris/azul/
# celeste, se lee mejor sobre fondo claro que sobre una franja oscura: se probo
# colocandolo sobre blanco, sobre el azul y sobre slate-900 antes de decidir).
AZUL = (37, 99, 235)        # #2563EB
CIAN = (56, 189, 248)       # #38BDF8
NAVY = (15, 23, 42)         # #0F172A
INK = NAVY
INK_SOFT = (100, 116, 139)  # #64748B
LINE = (226, 232, 240)      # #E2E8F0
SURFACE2 = (248, 250, 252)  # #F8FAFC
WHITE = (255, 255, 255)
LOGO_GESTIONALMACEN = "logo_gestionalmacen.png"
LOGO_SERVIGOD_BLANCO = "servigod_logo_blanco.png"

MARGIN = 15
PAGE_H = 297
CONTENT_W = 210 - MARGIN * 2
PIE_PAGINA_H = 10


def moneda(n):
    return "$" + f"{int(n):,}".replace(",", ".")


def cargar_playbook():
    with open(PLAYBOOK_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ajustar_fuente(pdf, textos, ancho_max, tam_inicial, tam_min=6.5, estilo=""):
    tam = tam_inicial
    while tam > tam_min:
        pdf.set_font("Helvetica", estilo, tam)
        if all(pdf.get_string_width(t) <= ancho_max for t in textos):
            return tam
        tam -= 0.2
    pdf.set_font("Helvetica", estilo, tam_min)
    return tam_min


def lineas_de_multicell(pdf, texto, ancho, tam, estilo=""):
    pdf.set_font("Helvetica", estilo, tam)
    lineas, actual = 1, ""
    for palabra in texto.split(" "):
        prueba = (actual + " " + palabra).strip()
        if pdf.get_string_width(prueba) <= ancho:
            actual = prueba
        else:
            lineas += 1
            actual = palabra
    return lineas


def bullet(pdf, x, y, texto, color_marca, tam, color_texto=None):
    pdf.set_fill_color(*color_marca)
    pdf.rect(x, y + 1.1, 1.6, 1.6, "F")
    pdf.set_xy(x + 3.2, y - 0.6)
    pdf.set_font("Helvetica", "", tam)
    pdf.set_text_color(*(color_texto or color_marca))
    pdf.cell(0, 4.2, texto)


def kicker_titulo(pdf, y, kicker, titulo, subtitulo=None):
    pdf.set_xy(MARGIN, y)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*AZUL)
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
    """Franja oscura fija (slate-900), igual en claro y en el resto del documento:
    aqui SI se usa fondo oscuro porque solo lleva texto blanco y el logo de ServiGod
    (que tiene una variante blanca hecha para esto), sin el logo detallado de
    GestionAlmacen que perdia nitidez sobre fondo oscuro."""
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, y, 210, h, "F")
    pdf.set_xy(MARGIN, y + 8)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 8, titulo)
    pdf.set_xy(MARGIN, y + 17)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(200, 210, 224)
    pdf.multi_cell(CONTENT_W, 4.8, texto)
    if extra:
        extra(pdf.get_y() + 3)


def construir_pdf(datos):
    empresa = datos.get("empresa", {})
    plan = datos.get("planes", [{}])[0]
    hechos = datos.get("hechos", [])
    beneficios = datos.get("beneficios", [])
    objeciones = {o["objecion"]: o["respuesta_sugerida"].strip() for o in datos.get("objeciones", [])}
    demo = datos.get("demo", {})
    trial_dias = empresa.get("trial_dias", 7)
    actualizado = datos.get("actualizado", "")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()

    # ---------- Encabezado (claro: el logo pierde nitidez sobre fondo oscuro) ----------
    pdf.set_fill_color(*SURFACE2)
    HERO_H = 62
    pdf.rect(0, 0, 210, HERO_H, "F")
    if os.path.exists(LOGO_GESTIONALMACEN):
        logo_w = 62
        logo_h = logo_w * 180 / 500  # proporcion real del logo (500x180)
        pdf.image(LOGO_GESTIONALMACEN, MARGIN, 10, w=logo_w, h=logo_h)
        ty = 10 + logo_h + 6
    else:
        ty = 14
    pdf.set_xy(MARGIN, ty)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.set_text_color(*AZUL)
    pdf.multi_cell(CONTENT_W, 4.2, "PUNTO DE VENTA PARA ALMACENES, MINIMARKETS Y BOTILLERIAS")
    pdf.set_xy(MARGIN, ty + 6)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*INK_SOFT)
    pdf.multi_cell(
        CONTENT_W, 4.6,
        "Convierte el celular en un punto de venta profesional: vende, controla el stock y "
        "lleva la caja de tu almacen, minimarket o botilleria sin equipos especiales.",
    )
    y = ty + 16.5
    chips = [f"{trial_dias} dias de prueba gratis", "Sin contrato anual", "Sin tarjeta de credito"]
    x = MARGIN
    for c in chips:
        bullet(pdf, x, y, c, AZUL, 8.3, color_texto=INK)
        pdf.set_font("Helvetica", "", 8.3)
        x += pdf.get_string_width(c) + 12

    # ---------- Que resuelve ----------
    y = HERO_H + 10
    y = kicker_titulo(pdf, y, "Que resuelve", "El control de tu negocio, desde el celular") + 3
    col_w = (CONTENT_W - 6) / 2
    tam_pilar = 8.3
    pares = [(beneficios[i], beneficios[i + 1] if i + 1 < len(beneficios) else "") for i in (0, 2, 4)]
    fila_y = y
    for fila in pares:
        alturas = [lineas_de_multicell(pdf, t, col_w, tam_pilar) for t in fila if t]
        alto_fila = max(alturas) * 3.9 if alturas else 0
        for i, texto in enumerate(fila):
            if not texto:
                continue
            px = MARGIN + i * (col_w + 6)
            pdf.set_xy(px, fila_y)
            bullet(pdf, px, fila_y + 1.5, "", AZUL, tam_pilar)  # solo la vinieta
            pdf.set_xy(px + 5, fila_y)
            pdf.set_font("Helvetica", "", tam_pilar + 0.7)
            pdf.set_text_color(*INK)
            pdf.multi_cell(col_w - 5, 4.4, texto)
        fila_y += alto_fila + 6
    y = fila_y + 4

    # ---------- El plan ----------
    y = kicker_titulo(pdf, y, "El plan", "Un solo plan, sin letra chica") + 2
    pad = 5
    incluye = plan.get("incluye", [])
    corte = (len(incluye) + 1) // 2
    mitad = CONTENT_W / 2
    tam_feat = ajustar_fuente(pdf, incluye, mitad - pad - 4, 9, 7.2)
    nota = plan.get("nota_oferta", "")
    lineas_nota = lineas_de_multicell(pdf, nota[0].upper() + nota[1:] + ".", CONTENT_W - pad * 2, 8.4) if nota else 0
    plan_h = pad + 6 + 8 + 9 + (lineas_nota * 4 + 3 if nota else 3) + corte * 4.8 + pad + 2
    pdf.set_draw_color(*AZUL)
    pdf.set_line_width(0.6)
    pdf.set_fill_color(*WHITE)
    pdf.rect(MARGIN, y, CONTENT_W, plan_h, "DF")
    pad = 5
    pdf.set_fill_color(*CIAN)
    pdf.rect(MARGIN + pad, y - 3.2, 24, 6, "F")
    pdf.set_xy(MARGIN + pad, y - 3.2)
    pdf.set_font("Helvetica", "B", 7.3)
    pdf.set_text_color(*INK)
    pdf.cell(24, 6, "PLAN UNICO", align="C")

    pad = pad  # (definido arriba, junto con incluye/corte/tam_feat/nota)
    ty = y + pad + 3
    pdf.set_xy(MARGIN + pad, ty)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    pdf.cell(0, 6, plan.get("nombre", "Plan Profesional").split(" (")[0])
    ty += 8
    pdf.set_xy(MARGIN + pad, ty)
    pdf.set_font("Helvetica", "B", 20)
    precio_txt = moneda(plan.get("precio_mensual_clp", 0))
    pdf.cell(pdf.get_string_width(precio_txt) + 2, 8, precio_txt)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*INK_SOFT)
    pdf.cell(0, 8, " " + plan.get("sufijo_precio", "/mes"))
    ty += 9
    if nota:
        pdf.set_xy(MARGIN + pad, ty)
        pdf.set_font("Helvetica", "", 8.4)
        pdf.set_text_color(*INK_SOFT)
        pdf.multi_cell(CONTENT_W - pad * 2, 4, nota[0].upper() + nota[1:] + ".")
        ty = pdf.get_y() + 3
    else:
        ty += 3

    y0 = ty
    for i, feat in enumerate(incluye):
        col = i // corte
        fila = i % corte
        bullet(pdf, MARGIN + pad + col * mitad, y0 + fila * 4.8, feat, AZUL, tam_feat, color_texto=INK)
    y = y + plan_h + 10

    # ---------- Como funciona (hechos confirmados) ----------
    if y + 60 > PAGE_H - PIE_PAGINA_H:
        pdf.add_page()
        y = MARGIN
    y = kicker_titulo(pdf, y, "Como funciona", "Datos confirmados del producto") + 3
    col_w = (CONTENT_W - 6) / 2
    tam_h = 8.4
    mitad_n = (len(hechos) + 1) // 2
    col1, col2 = hechos[:mitad_n], hechos[mitad_n:]
    y0 = y
    y_final = y
    for col_i, columna in enumerate((col1, col2)):
        yy = y0
        for h in columna:
            lp = lineas_de_multicell(pdf, h, col_w - 5, tam_h)
            bullet(pdf, MARGIN + col_i * (col_w + 6), yy + 1, h[:0], AZUL, tam_h)
            pdf.set_xy(MARGIN + col_i * (col_w + 6) + 3.2, yy - 0.6)
            pdf.set_font("Helvetica", "", tam_h)
            pdf.set_text_color(*INK)
            pdf.multi_cell(col_w - 3.2, 4.1, h)
            yy += lp * 4.1 + 3
        y_final = max(y_final, yy)
    y = y_final + 8

    # ---------- FAQ ----------
    preguntas = [
        "¿Cuánto cuesta? / Es caro",
        "¿Puedo probarlo antes de pagar?",
        "¿Tiene contrato o permanencia?",
        "¿Necesito comprar equipos o algo especial?",
        "Ya llevo las cuentas en un cuaderno, en Excel o de memoria",
    ]
    faq = [(p, objeciones[p]) for p in preguntas if p in objeciones]
    faltantes = [p for p in preguntas if p not in objeciones]
    if faltantes:
        print(f"AVISO: no se encontraron en el playbook estas preguntas (revisa el texto exacto): {faltantes}")
    faq_alturas = []
    for pregunta, respuesta in faq:
        lp = lineas_de_multicell(pdf, "?" + pregunta, CONTENT_W, 9.3, estilo="B")
        lr = lineas_de_multicell(pdf, respuesta, CONTENT_W, 8.6)
        faq_alturas.append(4 + lp * 4.3 + 1 + lr * 4 + 4)
    bloque_faq_h = 13 + sum(faq_alturas)
    if y + bloque_faq_h > PAGE_H - PIE_PAGINA_H - 40:
        pdf.add_page()
        y = MARGIN

    y = kicker_titulo(pdf, y, "Preguntas frecuentes", "Antes de decidir") + 2
    for pregunta, respuesta in faq:
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.25)
        pdf.line(MARGIN, y, MARGIN + CONTENT_W, y)
        y += 4
        pdf.set_xy(MARGIN, y)
        pdf.set_font("Helvetica", "B", 9.3)
        pdf.set_text_color(*INK)
        pdf.multi_cell(CONTENT_W, 4.3, pregunta)
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
        pdf.set_text_color(*WHITE)
        registro = empresa.get("registro_trial", "")
        pdf.cell(0, 5, f"Registrarme gratis: {registro}", link=registro if registro else "")
        pdf.set_xy(MARGIN, yy + 6)
        pdf.set_font("Helvetica", "", 8.6)
        pdf.set_text_color(200, 210, 224)
        detalle_demo = demo.get("modalidad", "Demo corta guiada por WhatsApp.")
        minutos = demo.get("duracion_minutos", "")
        pdf.cell(0, 4.5, detalle_demo + (f" {minutos} minutos." if minutos else ""))
        py = yy + 15
        pdf.set_draw_color(200, 210, 224)
        pdf.set_line_width(0.2)
        pdf.line(MARGIN, py, MARGIN + CONTENT_W, py)
        pdf.set_xy(MARGIN, py + 3.5)
        pdf.set_font("Helvetica", "", 7.6)
        pdf.set_text_color(200, 210, 224)
        pdf.cell(28, 5, "Un producto de")
        if os.path.exists(LOGO_SERVIGOD_BLANCO):
            sg_w = 26
            sg_h = sg_w * 350 / 1675
            pdf.image(LOGO_SERVIGOD_BLANCO, MARGIN + 26, py + 2, w=sg_w, h=sg_h)

    franja_banda(
        pdf, y, band_h, "¿Conversamos?",
        "Responde este mensaje por WhatsApp y coordinamos una demo corta sin costo, o "
        "registrate directamente para tu prueba gratis.",
        extra=cta_extra,
    )
    y += band_h + 4

    pdf.set_xy(MARGIN, y)
    pdf.set_font("Helvetica", "", 7.3)
    pdf.set_text_color(*INK_SOFT)
    pdf.cell(0, 4, f"Precios en pesos chilenos (CLP). Actualizado el {actualizado}.")

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
