"""
State machine persistida en JSON para el workflow de automatización.
Permite que el bot retome el estado si se reinicia.
"""
import json
from datetime import datetime
from pathlib import Path

_FILE = Path(__file__).parent.parent / "data" / "flujo_estado.json"


def _inicial() -> dict:
    return {
        "estado": "idle",
        "formato": "video",      # "video" (1920×1080, clips ≤60s) o "reel" (1080×1920, clips ≤15s)
        "tag_busqueda": None,    # hashtag elegido por el usuario (ej. "fails", "humor")
        "clips": [],             # clips seleccionados para la compilación
        "candidatos_pool": [],   # candidatos restantes — usados para reemplazar clips individuales
        "clips_descargados": [], # clips con ruta local
        "video_compilado": None,
        "video_preview": None,
        "seo": None,
        "recomendacion_horario": None,  # dict con dia_nombre, hora_local, razon, iso_utc
        "mensaje_id": None,
        "chat_id": None,
        "timestamp": None,
    }


def leer() -> dict:
    if _FILE.exists():
        try:
            with open(_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return _inicial()


def guardar(datos: dict):
    _FILE.parent.mkdir(exist_ok=True)
    datos["timestamp"] = datetime.now().isoformat()
    with open(_FILE, "w") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def reset():
    guardar(_inicial())


def actualizar(**kwargs):
    datos = leer()
    datos.update(kwargs)
    guardar(datos)
