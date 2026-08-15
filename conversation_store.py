"""
Capa de acceso a SQLite para el estado conversacional del agente
(mensajes entrantes/salientes y borradores de respuesta pendientes de aprobación).

Este store vive únicamente en el servicio siempre-activo (agent_service.py),
corriendo en el VPS. No se commitea a git: es estado vivo de un servicio con
escrituras en cualquier momento (llegan mensajes de WhatsApp a toda hora), a
diferencia de los CSV de leads que se actualizan por lotes periódicos desde
GitHub Actions.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.getenv("AGENT_DB_PATH", "agent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telefono_normalizado TEXT NOT NULL,
    direccion TEXT NOT NULL CHECK (direccion IN ('in', 'out')),
    texto TEXT NOT NULL,
    evolution_message_id TEXT,
    timestamp TEXT NOT NULL
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
    FOREIGN KEY (mensaje_entrante_id) REFERENCES messages (id)
);

CREATE INDEX IF NOT EXISTS idx_drafts_estado ON drafts (estado);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def inicializar_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)


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


def crear_borrador(telefono_normalizado, mensaje_entrante_id, texto_borrador):
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO drafts (telefono_normalizado, mensaje_entrante_id, texto_borrador, estado, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (telefono_normalizado, mensaje_entrante_id, texto_borrador, _ahora()),
        )
        return cur.lastrowid


def listar_borradores_pendientes():
    with _conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.telefono_normalizado, d.texto_borrador, d.created_at, "
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
