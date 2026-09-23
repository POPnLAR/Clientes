"""
Capa de acceso a SQLite para el estado conversacional del agente
(mensajes entrantes/salientes y borradores de respuesta pendientes de aprobación).

Este store vive únicamente en el servicio siempre-activo (agent_service.py),
corriendo en el VPS. No se commitea a git: es estado vivo de un servicio con
escrituras en cualquier momento (llegan mensajes de WhatsApp a toda hora), a
diferencia de los CSV de leads que se actualizan por lotes periódicos desde
GitHub Actions.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.getenv("AGENT_DB_PATH", "agent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telefono_normalizado TEXT NOT NULL,
    direccion TEXT NOT NULL CHECK (direccion IN ('in', 'out')),
    texto TEXT NOT NULL,
    evolution_message_id TEXT,
    timestamp TEXT NOT NULL,
    filtrado_motivo TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_telefono ON messages (telefono_normalizado);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telefono_normalizado TEXT NOT NULL,
    mensaje_entrante_id INTEGER,
    texto_borrador TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pending' CHECK (estado IN ('pending', 'approved', 'rejected', 'sent')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    accion_tipo TEXT,
    accion_payload TEXT,
    accion_resultado TEXT,
    FOREIGN KEY (mensaje_entrante_id) REFERENCES messages (id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_estado ON drafts (estado);

CREATE TABLE IF NOT EXISTS bots_aprendidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    texto TEXT NOT NULL,
    texto_normalizado TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citas_agendadas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    telefono_normalizado TEXT NOT NULL,
    google_event_id TEXT NOT NULL,
    inicio_iso TEXT NOT NULL,
    fin_iso TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES drafts (id)
);
"""

# Migración para bases de datos ya existentes en producción, creadas antes de
# que 'drafts' tuviera estas columnas (CREATE TABLE IF NOT EXISTS no las
# agrega a una tabla que ya existe).
_MIGRACIONES_DRAFTS = {
    "accion_tipo": "ALTER TABLE drafts ADD COLUMN accion_tipo TEXT",
    "accion_payload": "ALTER TABLE drafts ADD COLUMN accion_payload TEXT",
    "accion_resultado": "ALTER TABLE drafts ADD COLUMN accion_resultado TEXT",
}


_MIGRACIONES_MESSAGES = {
    "filtrado_motivo": "ALTER TABLE messages ADD COLUMN filtrado_motivo TEXT",
}


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrar_columnas(conn, tabla, migraciones):
    columnas_actuales = {row["name"] for row in conn.execute(f"PRAGMA table_info({tabla})")}
    for columna, ddl in migraciones.items():
        if columna not in columnas_actuales:
            conn.execute(ddl)


def inicializar_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)
        _migrar_columnas(conn, "drafts", _MIGRACIONES_DRAFTS)
        _migrar_columnas(conn, "messages", _MIGRACIONES_MESSAGES)


def _ahora():
    return datetime.utcnow().isoformat()


def guardar_mensaje(telefono_normalizado, direccion, texto, evolution_message_id=None):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (telefono_normalizado, direccion, texto, evolution_message_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (telefono_normalizado, direccion, texto, evolution_message_id, _ahora()),
        )
        return cur.lastrowid


def historial_conversacion(telefono_normalizado, limite=20):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT direccion, texto, timestamp FROM messages "
            "WHERE telefono_normalizado = ? ORDER BY id DESC LIMIT ?",
            (telefono_normalizado, limite),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def crear_borrador(telefono_normalizado, mensaje_entrante_id, texto_borrador, accion_tipo=None, accion_payload=None):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO drafts (telefono_normalizado, mensaje_entrante_id, texto_borrador, estado, "
            "created_at, accion_tipo, accion_payload) VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (
                telefono_normalizado,
                mensaje_entrante_id,
                texto_borrador,
                _ahora(),
                accion_tipo,
                json.dumps(accion_payload, ensure_ascii=False) if accion_payload else None,
            ),
        )
        return cur.lastrowid


def listar_borradores_pendientes():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.telefono_normalizado, d.texto_borrador, d.created_at, "
            "       d.accion_tipo, d.accion_payload, "
            "       m.texto AS mensaje_entrante, m.timestamp AS mensaje_entrante_timestamp "
            "FROM drafts d "
            "LEFT JOIN messages m ON m.id = d.mensaje_entrante_id "
            "WHERE d.estado = 'pending' "
            "ORDER BY d.created_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def obtener_borrador(draft_id):
    with _conn() as conn:
        row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return dict(row) if row else None


def marcar_borrador(draft_id, estado):
    with _conn() as conn:
        conn.execute(
            "UPDATE drafts SET estado = ?, decided_at = ? WHERE id = ?",
            (estado, _ahora(), draft_id),
        )


def marcar_resultado_accion(draft_id, resultado):
    with _conn() as conn:
        conn.execute(
            "UPDATE drafts SET accion_resultado = ? WHERE id = ?",
            (resultado, draft_id),
        )


def registrar_cita_agendada(draft_id, telefono_normalizado, google_event_id, inicio_iso, fin_iso):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO citas_agendadas (draft_id, telefono_normalizado, google_event_id, "
            "inicio_iso, fin_iso, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (draft_id, telefono_normalizado, google_event_id, inicio_iso, fin_iso, _ahora()),
        )
        return cur.lastrowid


# --- Consultas del filtro de mensajes (ver filtro_mensajes.py) ---

def _hace(minutos):
    return (datetime.utcnow() - timedelta(minutes=minutos)).isoformat()


def existe_mensaje_evolution(evolution_message_id):
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE evolution_message_id = ? LIMIT 1",
            (evolution_message_id,),
        ).fetchone()
    return row is not None


def marcar_mensaje_filtrado(mensaje_id, motivo):
    with _conn() as conn:
        conn.execute("UPDATE messages SET filtrado_motivo = ? WHERE id = ?", (motivo, mensaje_id))


def tiene_mensajes_salientes(telefono_normalizado):
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE telefono_normalizado = ? AND direccion = 'out' LIMIT 1",
            (telefono_normalizado,),
        ).fetchone()
    return row is not None


def ultimo_mensaje_saliente(telefono_normalizado):
    with _conn() as conn:
        row = conn.execute(
            "SELECT texto FROM messages WHERE telefono_normalizado = ? AND direccion = 'out' "
            "ORDER BY id DESC LIMIT 1",
            (telefono_normalizado,),
        ).fetchone()
    return row["texto"] if row else None


def contar_entrantes_desde(telefono_normalizado, minutos):
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE telefono_normalizado = ? AND direccion = 'in' "
            "AND timestamp >= ?",
            (telefono_normalizado, _hace(minutos)),
        ).fetchone()[0]


def contar_texto_repetido(telefono_normalizado, texto, minutos):
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE telefono_normalizado = ? AND direccion = 'in' "
            "AND lower(trim(texto)) = lower(trim(?)) AND timestamp >= ?",
            (telefono_normalizado, texto, _hace(minutos)),
        ).fetchone()[0]


def hay_entrante_posterior(telefono_normalizado, mensaje_id):
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM messages WHERE telefono_normalizado = ? AND direccion = 'in' "
            "AND id > ? LIMIT 1",
            (telefono_normalizado, mensaje_id),
        ).fetchone()
    return row is not None


def listar_filtrados(limite=50, dias=None):
    where = "filtrado_motivo IS NOT NULL"
    params = []
    if dias:
        where += " AND timestamp >= ?"
        params.append(_hace(dias * 24 * 60))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, telefono_normalizado, texto, timestamp, filtrado_motivo FROM messages "
            f"WHERE {where} ORDER BY id DESC LIMIT ?",
            (*params, limite),
        ).fetchall()
    return [dict(r) for r in rows]


def resumen_filtros(dias=7):
    """Entrantes del período, cuántos se filtraron (y por qué) y el detalle por día."""
    desde = _hace(dias * 24 * 60)
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direccion = 'in' AND timestamp >= ?", (desde,)
        ).fetchone()[0]
        por_motivo = {
            r["filtrado_motivo"]: r["n"]
            for r in conn.execute(
                "SELECT filtrado_motivo, COUNT(*) AS n FROM messages WHERE direccion = 'in' "
                "AND filtrado_motivo IS NOT NULL AND timestamp >= ? GROUP BY filtrado_motivo",
                (desde,),
            )
        }
        por_dia = [
            dict(r)
            for r in conn.execute(
                "SELECT substr(timestamp, 1, 10) AS dia, COUNT(*) AS entrantes, "
                "SUM(CASE WHEN filtrado_motivo IS NOT NULL THEN 1 ELSE 0 END) AS filtrados "
                "FROM messages WHERE direccion = 'in' AND timestamp >= ? "
                "GROUP BY dia ORDER BY dia",
                (desde,),
            )
        ]
    filtrados = sum(por_motivo.values())
    return {
        "dias": dias,
        "entrantes": total,
        "filtrados": filtrados,
        "a_gemini": total - filtrados,
        "por_motivo": por_motivo,
        "por_dia": por_dia,
    }


def telefonos_que_respondieron(dias=90):
    """
    Teléfonos (no @lid) que escribieron de verdad al WhatsApp de ventas. No cuentan las
    autorespuestas de bots ni los mensajes repetidos/flood: un bot contestando no es un
    lead conversando.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT telefono_normalizado FROM messages "
            "WHERE direccion = 'in' AND timestamp >= ? AND telefono_normalizado NOT LIKE '%@%' "
            "AND (filtrado_motivo IS NULL OR filtrado_motivo IN ('cierre', 'rafaga'))",
            (_hace(dias * 24 * 60),),
        ).fetchall()
    return [r["telefono_normalizado"] for r in rows]


def reclasificar_como_bot(es_bot, limite=5000):
    """
    Marca como 'bot' los mensajes entrantes antiguos (sin motivo) que las reglas actuales ya
    reconocen como bot. Se ejecuta al iniciar el servicio: los patrones mejoran con el tiempo y
    así los mensajes anteriores a una mejora también quedan bien clasificados. Idempotente.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, texto FROM messages WHERE direccion = 'in' AND filtrado_motivo IS NULL "
            "ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
        ids = [(r["id"],) for r in rows if es_bot(r["texto"])]
        conn.executemany("UPDATE messages SET filtrado_motivo = 'bot' WHERE id = ?", ids)
    return len(ids)


def obtener_mensaje(mensaje_id):
    if mensaje_id is None:
        return None
    with _conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (mensaje_id,)).fetchone()
    return dict(row) if row else None


def actualizar_borrador(draft_id, texto_borrador, accion_tipo=None, accion_payload=None):
    """Reemplaza el texto (y la acción) de un borrador pendiente, p. ej. al regenerarlo."""
    with _conn() as conn:
        conn.execute(
            "UPDATE drafts SET texto_borrador = ?, accion_tipo = ?, accion_payload = ? "
            "WHERE id = ? AND estado = 'pending'",
            (
                texto_borrador,
                accion_tipo,
                json.dumps(accion_payload, ensure_ascii=False) if accion_payload else None,
                draft_id,
            ),
        )


# --- Bots que el operador enseñó desde el dashboard ("🤖 Es un bot") ---

def agregar_bot_aprendido(texto, texto_normalizado):
    """Guarda un mensaje como bot conocido. Devuelve su id (el existente si ya estaba)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM bots_aprendidos WHERE texto_normalizado = ?", (texto_normalizado,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO bots_aprendidos (texto, texto_normalizado, created_at) VALUES (?, ?, ?)",
            (texto, texto_normalizado, _ahora()),
        )
        return cur.lastrowid


def listar_bots_aprendidos():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, texto, texto_normalizado, created_at FROM bots_aprendidos ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def eliminar_bot_aprendido(bot_id):
    """Quita el bot de la lista. Devuelve True si existía."""
    with _conn() as conn:
        cur = conn.execute("DELETE FROM bots_aprendidos WHERE id = ?", (bot_id,))
        return cur.rowcount > 0
