# --- CICLO PRINCIPAL CON VALIDACIÓN DE 24 HORAS PARA TODA LA SECUENCIA ---
def ejecutar_ciclo():
    ahora = datetime.now()
    if ahora.weekday() > 5 or not (9 <= ahora.hour <= 19): 
        print("Fuera de horario de envío (Lunes-Sábado 09:00-20:00)")
        return 

    columnas = ["Id","Fecha","Hora","Evento","Ministerio","Ubicacion","Estado","Telefono","Email","Email_Enviado","Dia_Secuencia","Fecha_Contacto"]
    
    if not os.path.exists(ARCHIVO_LEADS):
        df = pd.DataFrame(columns=columnas)
    else:
        df = pd.read_csv(ARCHIVO_LEADS)
        df["Dia_Secuencia"] = pd.to_numeric(df["Dia_Secuencia"], errors='coerce').fillna(0).astype(int)
        for col in columnas:
            if col not in df.columns: 
                df[col] = "No" if col == "Email_Enviado" else ("" if col != "Dia_Secuencia" else 0)
    
    hoy = ahora.strftime("%d/%m/%Y")
    candidatos = []
    
    print(f"--- Revisión de Seguridad (>24h) iniciada: {ahora.strftime('%H:%M')} ---")

    for idx, row in df.iterrows():
        # 1. Saltar si ya se procesó en esta misma ventana (string check)
        if hoy in str(row.get('Fecha_Contacto', '')):
            continue
            
        # 2. Ignorar estados que no requieren acción
        if row["Estado"] in ["Finalizado", "Rechazado", "Cita Agendada"]:
            continue

        dia_act = int(row.get("Dia_Secuencia", 0))
        
        # --- REGLA DE ORO: VALIDACIÓN DE 24 HORAS PARA SEGUIMIENTOS (Día 2, 3 y 4) ---
        if row["Estado"] == "Contactado":
            fecha_str = str(row.get('Fecha_Contacto', ''))
            try:
                # Convertimos el último contacto a objeto datetime
                ultima_fecha = datetime.strptime(fecha_str, "%d/%m/%Y %H:%M")
                diferencia = ahora - ultima_fecha
                
                # Si han pasado menos de 24 horas, no se añade a candidatos
                if diferencia < timedelta(hours=24):
                    # Opcional: print(f"⏳ {row['Evento']} esperando 24h...")
                    continue
            except ValueError:
                # Si hay error en fecha y ya está contactado, esperamos por seguridad
                if fecha_str != "": continue

        # ASIGNACIÓN DE SIGUIENTE PASO
        if row["Estado"] == "Contactado" and dia_act < 4:
            # Si pasó la validación de 24h, va al siguiente día (2, 3 o 4)
            candidatos.append({'idx': idx, 'dia': dia_act + 1})
        elif row["Estado"] == "Nuevo":
            # Los nuevos no requieren validación de 24h previa
            candidatos.append({'idx': idx, 'dia': 1})

    # Si no hay nadie listo para seguimiento, buscamos nuevos
    if not candidatos:
        print("Sin seguimientos pendientes de >24h. Buscando sangre nueva...")
        df = buscar_y_agregar_nuevos(df)
        for idx, row in df.iterrows():
            if row["Estado"] == "Nuevo" and len(candidatos) < 20:
                candidatos.append({'idx': idx, 'dia': 1})

    print(f"Total candidatos listos para hoy: {len(candidatos[:20])}")

    for item in candidatos[:20]:
        idx, dia_obj = item['idx'], item['dia']
        row = df.loc[idx]
        
        tel = "".join(filter(str.isdigit, str(row["Telefono"])))
        if len(tel) == 9: tel = "56" + tel
        
        msg = obtener_mensaje_secuencia(row["Evento"], row["Ubicacion"], dia_obj)
        
        print(f"Enviando Día {dia_obj} a: {row['Evento']}")
        
        if enviar_mensaje_texto(tel, msg):
            df.at[idx, "Estado"] = "Contactado" if dia_obj < 4 else "Finalizado"
            df.at[idx, "Dia_Secuencia"] = dia_obj
            df.at[idx, "Fecha_Contacto"] = ahora.strftime("%d/%m/%Y %H:%M")
            
            # Guardado progresivo
            df.to_csv(ARCHIVO_LEADS, index=False)
            print(f"✅ Registro actualizado localmente.")
            
            time.sleep(random.randint(120, 300))
        else:
            print(f"❌ Error en envío a {tel}")

if __name__ == "__main__":
    ejecutar_ciclo()