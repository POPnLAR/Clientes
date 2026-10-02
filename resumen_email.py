"""
Resumen por correo de cada ciclo de los workers (clínicas y almacenes).

Se envía por Gmail SMTP con una "contraseña de aplicación" de Google (no la contraseña normal de
la cuenta): GMAIL_USER, GMAIL_APP_PASSWORD y, opcionalmente, EMAIL_DESTINO. Si faltan, se omite
en silencio: el correo es un extra y nunca debe hacer fallar el ciclo de prospección.
"""
import logging
import os
import smtplib
from email.mime.text import MIMEText

EMAIL_DESTINO_POR_DEFECTO = "rvillegasburgos@gmail.com"


def nuevo_resumen():
    return {"nuevos_leads": [], "mensajes": [], "reciclados": 0, "alertas": []}


def hay_algo_que_reportar(resumen):
    return bool(resumen["nuevos_leads"] or resumen["mensajes"] or resumen["reciclados"] or resumen["alertas"])


def armar_cuerpo(resumen):
    lineas = []

    if resumen["alertas"]:
        lineas.append("ALERTAS:")
        lineas += [f"- {a}" for a in resumen["alertas"]]
        lineas.append("")

    if resumen["nuevos_leads"]:
        lineas.append(f"Leads nuevos encontrados ({len(resumen['nuevos_leads'])}):")
        lineas += [f"- {l['Evento']} ({l['Ubicacion']})" for l in resumen["nuevos_leads"]]
        lineas.append("")

    if resumen["mensajes"]:
        exitosos = [m for m in resumen["mensajes"] if m["ok"]]
        fallidos = [m for m in resumen["mensajes"] if not m["ok"]]
        lineas.append(f"Mensajes de secuencia enviados ({len(exitosos)} ok, {len(fallidos)} fallidos):")
        for m in resumen["mensajes"]:
            lineas.append(f"- [{'OK' if m['ok'] else 'FALLO'}] {m['Evento']} - Día {m['dia']}")
        lineas.append("")

    if resumen["reciclados"]:
        lineas.append(f"Leads reciclados para recontacto: {resumen['reciclados']}")

    return "\n".join(lineas)


def enviar_resumen_email(asunto, cuerpo):
    """Devuelve True si el correo salió. Nunca lanza."""
    usuario = os.getenv("GMAIL_USER")
    clave = os.getenv("GMAIL_APP_PASSWORD")
    destino = os.getenv("EMAIL_DESTINO") or EMAIL_DESTINO_POR_DEFECTO
    if not usuario or not clave:
        logging.warning("GMAIL_USER/GMAIL_APP_PASSWORD no configurados, se omite el resumen por email.")
        return False

    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = destino
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            server.login(usuario, clave)
            server.sendmail(usuario, [destino], msg.as_string())
        logging.info("Resumen del ciclo enviado por email a %s.", destino)
        return True
    except Exception:
        logging.exception("No se pudo enviar el resumen del ciclo por email.")
        return False


def enviar_resumen_si_corresponde(resumen, prefijo_asunto, ahora):
    """Solo manda el correo si pasó algo relevante en el ciclo (ciclo vacío o fuera de horario: nada)."""
    if not hay_algo_que_reportar(resumen):
        print("📪 Nada relevante que reportar este ciclo, no se envía resumen por email.")
        return False
    asunto = f"{prefijo_asunto} - Resumen {ahora.strftime('%d/%m %H:%M')}"
    return enviar_resumen_email(asunto, armar_cuerpo(resumen))
