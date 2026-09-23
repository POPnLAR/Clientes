"""
Vincula la sesión de WhatsApp de una instancia de Evolution API (v2) escaneando el QR.

Uso:
    python evo_qr.py                 # estado + QR si no está conectada, espera hasta 'open'
    python evo_qr.py --estado        # solo consulta el estado
    python evo_qr.py --crear         # crea la instancia si no existe
    python evo_qr.py --numero 569XXXXXXXX   # pide código de emparejamiento además del QR

Lee EVO_URL, EVO_TOKEN y EVO_INSTANCE del entorno (o de un archivo .env en esta carpeta, o el indicado con --env).
"""
import argparse
import base64
import os
import sys
import time
import webbrowser

import requests

from evo_client import verificar_estado_conexion

QR_PATH = "evo_qr.png"


def cargar_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def crear_instancia(base, token, instance):
    res = requests.post(
        f"{base}/instance/create",
        json={"instanceName": instance, "qrcode": True, "integration": "WHATSAPP-BAILEYS"},
        headers={"apikey": token},
        timeout=30,
    )
    print(f"Crear instancia: HTTP {res.status_code} {res.text[:300]}")
    return res.status_code in (200, 201)


def pedir_qr(base, token, instance, numero=None):
    """Devuelve el JSON de /instance/connect (base64 del QR y/o pairingCode)."""
    params = {"number": numero} if numero else None
    res = requests.get(
        f"{base}/instance/connect/{instance}",
        params=params,
        headers={"apikey": token},
        timeout=30,
    )
    if res.status_code != 200:
        print(f"connect: HTTP {res.status_code} {res.text[:300]}")
        return None
    return res.json()


def guardar_qr(data):
    b64 = data.get("base64")
    if not b64:
        return False
    with open(QR_PATH, "wb") as f:
        f.write(base64.b64decode(b64.split(",", 1)[-1]))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--estado", action="store_true", help="solo consultar estado")
    ap.add_argument("--crear", action="store_true", help="crear la instancia si no existe")
    ap.add_argument("--numero", help="número (569...) para obtener pairing code")
    ap.add_argument("--env", default=".env", help="archivo con EVO_URL/EVO_TOKEN/EVO_INSTANCE (ej. .env.v2)")
    ap.add_argument("--espera", type=int, default=180, help="segundos máximos esperando el escaneo")
    args = ap.parse_args()

    cargar_env(args.env)
    url, token, instance = (os.getenv(k) for k in ("EVO_URL", "EVO_TOKEN", "EVO_INSTANCE"))
    if not (url and token and instance):
        sys.exit("Faltan EVO_URL, EVO_TOKEN o EVO_INSTANCE (entorno o .env).")
    base = url.strip().rstrip("/")

    estado = verificar_estado_conexion(base, instance, token)
    print(f"Estado de '{instance}': {estado}")
    if estado == "open":
        print("Ya está vinculada. Nada que hacer.")
        return
    if args.estado:
        return

    if estado == "unknown":
        if args.crear:
            if not crear_instancia(base, token, instance):
                sys.exit("No se pudo crear la instancia.")
        else:
            print("Estado desconocido: puede que la instancia no exista (usa --crear) "
                  "o que URL/apikey sean incorrectas.")

    intentos_qr = 0
    limite = time.time() + args.espera
    while time.time() < limite:
        data = pedir_qr(base, token, instance, args.numero)
        if data is None:
            sys.exit("No se pudo obtener el QR. Revisa URL, apikey e instancia.")
        if data.get("pairingCode"):
            print(f"Código de emparejamiento: {data['pairingCode']} "
                  "(WhatsApp > Dispositivos vinculados > Vincular con número de teléfono)")
        if guardar_qr(data):
            intentos_qr += 1
            print(f"QR #{intentos_qr} guardado en {os.path.abspath(QR_PATH)} — escanéalo desde "
                  "WhatsApp > Dispositivos vinculados.")
            if intentos_qr == 1:
                webbrowser.open(os.path.abspath(QR_PATH))
        else:
            print("Sin QR en la respuesta:", str(data)[:200])

        # El QR de Baileys caduca en ~30-60 s: sondea el estado y renueva.
        for _ in range(10):
            time.sleep(3)
            if verificar_estado_conexion(base, instance, token) == "open":
                print("OK - Sesión vinculada (estado: open).")
                return
    sys.exit("Tiempo agotado sin que se escaneara el QR.")


if __name__ == "__main__":
    main()
