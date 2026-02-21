import pandas as pd
import requests
import os
import random
import time
import unicodedata
from datetime import datetime
from fpdf import FPDF

# --- CONFIGURACIÓN ---
EVO_URL = os.getenv("EVO_URL")
EVO_TOKEN = os.getenv("EVO_TOKEN")
EVO_INSTANCE = os.getenv("EVO_INSTANCE")
SERP_KEY = os.getenv("SERP_KEY")
ARCHIVO_LEADS = "prospeccion_gestionvital_pro.csv"

def limpiar_texto(t):
    return "".join(c for c in unicodedata.normalize('NFD', str(t)) if unicodedata.category(c) != 'Mn')

def enviar_whatsapp(numero, mensaje):
    url = f"{EVO_URL.strip().rstrip('/')}/message/sendText/{EVO_INSTANCE}"
    headers = {"Content-Type": "application/json", "apikey": EVO_TOKEN}
    payload = {"number": numero, "options": {"delay": 1200}, "textMessage": {"text": mensaje}}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        return res.status_code in [200, 201]
    except: return False

def ejecutar_ciclo():
    ahora = datetime.now()
    # 1. Validación de Horario (Lunes a Sábado, 09:00 a 19:00)
    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19):
        print("Fuera de horario de oficina. Saltando ciclo.")
        return

    if not os.path.exists(ARCHIVO_LEADS):
        print("No hay base de datos para procesar.")
        return

    df = pd.read_csv(ARCHIVO_LEADS)
    
    # 2. Filtrar quienes necesitan seguimiento (Día 2 o 3)
    # Solo procesamos uno por ciclo para ser "silenciosos" y evitar bloqueos
    pendientes = df[(df["Estado"] == "Contactado") & (df["Dia_Secuencia"] < 3)].head(1)
    
    if not pendientes.empty:
        idx = pendientes.index[0]
        proximo_dia = int(pendientes.at[idx, "Dia_Secuencia"]) + 1
        
        # Aquí llamarías a tu lógica de mensajes mejorados
        mensaje = f"Hola! Siguiendo con lo anterior..." # Simplificado para el ejemplo
        tel = "".join(filter(str.isdigit, str(pendientes.at[idx, "Telefono"])))
        
        if enviar_whatsapp(tel, mensaje):
            df.at[idx, "Dia_Secuencia"] = proximo_dia
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            print(f"Seguimiento enviado a {pendientes.at[idx, 'Evento']}")
            df.to_csv(ARCHIVO_LEADS, index=False)

if __name__ == "__main__":
    ejecutar_ciclo()