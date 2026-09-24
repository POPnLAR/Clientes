"""
Control de acceso del dashboard: login con Google (OIDC integrado de Streamlit) + lista de correos
permitidos. Falla CERRADO: si algo falta o está mal configurado, no se muestra nada de la app.

Configuración (Secrets de Streamlit; ver .streamlit/secrets.toml.example):
    ALLOWED_EMAILS = "correo1@gmail.com,correo2@gmail.com"   # quién puede entrar
    [auth] redirect_uri, cookie_secret, client_id, client_secret, server_metadata_url
"""
import streamlit as st

_CLAVES_AUTH = ("redirect_uri", "cookie_secret", "client_id", "client_secret", "server_metadata_url")


def correos_permitidos(valor):
    """Acepta "a@x.com, b@y.com" o una lista; devuelve el conjunto en minúsculas."""
    if not valor:
        return set()
    if isinstance(valor, str):
        valor = valor.replace(";", ",").split(",")
    return {str(c).strip().lower() for c in valor if str(c).strip()}


def auth_configurado(secretos):
    """True si la sección [auth] de los secretos trae todo lo que Streamlit necesita."""
    try:
        auth = secretos.get("auth")
        return bool(auth) and all(str(auth.get(k, "")).strip() for k in _CLAVES_AUTH)
    except Exception:
        return False


def _valor(usuario, campo, defecto=None):
    try:
        return usuario.get(campo, defecto)
    except Exception:
        return getattr(usuario, campo, defecto)


def evaluar_acceso(usuario, permitidos, configurado):
    """
    Devuelve el estado de acceso:
      "sin_configurar" -> falta la sección [auth] de los secretos
      "sin_correos"    -> no hay ningún correo permitido definido
      "login"          -> todavía no inició sesión
      "no_verificado"  -> inició sesión con un correo que el proveedor no verificó
      "no_autorizado"  -> inició sesión, pero su correo no está en la lista
      "ok"             -> acceso concedido
    """
    if not configurado:
        return "sin_configurar"
    if not permitidos:
        return "sin_correos"
    if not _valor(usuario, "is_logged_in", False):
        return "login"
    if _valor(usuario, "email_verified", False) is not True:
        return "no_verificado"
    correo = str(_valor(usuario, "email", "") or "").strip().lower()
    return "ok" if correo in permitidos else "no_autorizado"


def exigir_login():
    """
    Llamar al inicio de la app, ANTES de leer o mostrar cualquier dato. Si el acceso no está
    concedido, dibuja la pantalla correspondiente y detiene la ejecución (st.stop). Si lo está,
    muestra en la barra lateral quién inició sesión y devuelve su correo.
    """
    permitidos = correos_permitidos(st.secrets.get("ALLOWED_EMAILS", ""))
    estado = evaluar_acceso(st.user, permitidos, auth_configurado(st.secrets))

    if estado == "ok":
        correo = str(st.user.get("email", "")).strip().lower()
        with st.sidebar:
            st.caption(f"🔐 Sesión: {correo}")
            st.button("Cerrar sesión", on_click=st.logout, key="acceso_cerrar_sesion")
        return correo

    st.title("🔐 GestiónVital Pro")
    if estado == "sin_configurar":
        st.error(
            "Acceso no configurado: falta la sección `[auth]` en los Secrets de la app "
            "(ver `.streamlit/secrets.toml.example`). Por seguridad no se muestra nada."
        )
    elif estado == "sin_correos":
        st.error(
            "Acceso no configurado: falta `ALLOWED_EMAILS` en los Secrets de la app. "
            "Por seguridad no se muestra nada."
        )
    elif estado == "login":
        st.info("Este panel es privado. Inicia sesión con tu cuenta de Google para continuar.")
        st.button("Iniciar sesión con Google", on_click=st.login, type="primary", key="acceso_login")
    elif estado == "no_verificado":
        st.error("Tu cuenta de Google no tiene el correo verificado, así que no se puede dar acceso.")
        st.button("Cerrar sesión", on_click=st.logout, key="acceso_cerrar_sesion_nv")
    else:  # no_autorizado
        st.error(f"La cuenta {st.user.get('email', '')} no tiene acceso a este panel.")
        st.button("Cerrar sesión", on_click=st.logout, key="acceso_cerrar_sesion_na")
    st.stop()
