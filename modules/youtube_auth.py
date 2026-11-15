"""
Autenticación con YouTube — única fuente de verdad del token y de los scopes.

Lo usan tanto la web (uploader.py, video_manager.py) como el camino headless
(workflow.py, telegram_bot.py, cargar_historial_titulos.py). A propósito NO importa
streamlit: el bot lo carga en un proceso sin interfaz.

Antes había una copia de esto en cada llamador — seis en total — y una de ellas
(`/stats`) pedía 2 scopes en vez de 3, y otra (el Subidor) resolvía el token
relativo al cwd. Si hay que cambiar los scopes, ahora se cambia acá y nada más.
"""

from pathlib import Path

# Permisos para subir videos, leer los datos del canal y comentar (force-ssl).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Absoluto: el token vive en la raíz del proyecto, no en el directorio desde el que
# se lanzó Streamlit o el bot.
TOKEN_FILE = Path(__file__).parent.parent / "token_youtube.json"


def hay_token() -> bool:
    """True si el archivo de token existe. No dice nada sobre si es válido."""
    return TOKEN_FILE.exists()


def guardar_credenciales(credenciales) -> None:
    """Persiste el token (después de autenticar o de refrescar)."""
    with open(TOKEN_FILE, "w") as f:
        f.write(credenciales.to_json())


def cargar_credenciales(persistir: bool = True):
    """
    Credenciales guardadas, o None si no hay token o si quedaron inválidas.

    Si el token expiró y hay refresh_token, lo renueva. Con persistir=False no
    reescribe el archivo — lo usan los scripts de solo lectura.

    Propaga la excepción si el archivo está corrupto o si falla el refresh: el
    llamador decide si eso es un error fatal o un simple "no conectado".
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not hay_token():
        return None

    credenciales = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if credenciales.expired and credenciales.refresh_token:
        credenciales.refresh(Request())
        if persistir:
            guardar_credenciales(credenciales)
    return credenciales if credenciales.valid else None


def cliente_youtube(persistir: bool = True):
    """Cliente de la YouTube Data API v3, o None si no hay credenciales válidas."""
    from googleapiclient.discovery import build

    credenciales = cargar_credenciales(persistir=persistir)
    if not credenciales:
        return None
    return build("youtube", "v3", credentials=credenciales)


def borrar_token() -> None:
    """Borra el token guardado para forzar una autenticación nueva."""
    TOKEN_FILE.unlink(missing_ok=True)
