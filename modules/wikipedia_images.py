"""
Módulo — Búsqueda de imágenes de personas famosas en Wikipedia.

Wikipedia/Wikimedia Commons tiene fotos de prácticamente todos los futbolistas
y figuras públicas bajo licencia Creative Commons (legales para monetización
en YouTube). A diferencia de Pexels que solo tiene stock genérico, acá podemos
obtener una foto real de Zlatan, Messi, Maradona, etc.

API: https://en.wikipedia.org/api/rest_v1/page/summary/{name}
"""

import os
import re
from pathlib import Path

import requests

USER_AGENT = "YoutubeAIManager/1.0 (https://github.com/agumarini94/youtube-ai-manager)"
WIKI_REST_BASE_ES = "https://es.wikipedia.org/api/rest_v1/page/summary/"
WIKI_REST_BASE_EN = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def _normalizar_para_url(nombre: str) -> str:
    """Convierte 'Zlatan Ibrahimović' → 'Zlatan_Ibrahimović' para el endpoint."""
    return nombre.strip().replace(" ", "_")


def _consultar_resumen(nombre: str, idioma: str = "es") -> dict | None:
    """Consulta el endpoint REST de Wikipedia. Retorna dict o None si no existe."""
    base = WIKI_REST_BASE_ES if idioma == "es" else WIKI_REST_BASE_EN
    url = base + _normalizar_para_url(nombre)
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=12,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("type") == "disambiguation":
            return None
        return data
    except Exception:
        return None


def _url_imagen_alta_resolucion(resumen: dict) -> str | None:
    """De un resumen REST, devuelve la URL de la imagen más grande disponible."""
    if not resumen:
        return None
    original = resumen.get("originalimage") or {}
    if original.get("source"):
        return original["source"]
    thumb = resumen.get("thumbnail") or {}
    return thumb.get("source")


def buscar_url_imagen(nombre: str) -> str | None:
    """
    Busca la imagen principal del artículo de Wikipedia sobre `nombre`.
    Intenta primero en español, después en inglés. Retorna URL o None.
    """
    if not nombre or len(nombre.strip()) < 2:
        return None

    for idioma in ("es", "en"):
        resumen = _consultar_resumen(nombre, idioma=idioma)
        url = _url_imagen_alta_resolucion(resumen)
        if url:
            return url
    return None


def _slug(nombre: str) -> str:
    """Genera un slug seguro para nombre de archivo."""
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", nombre.strip().lower())
    return s.strip("_")[:50] or "wiki_img"


def descargar_imagen(nombre: str, carpeta: str) -> str | None:
    """
    Descarga la imagen del personaje en `carpeta`. Devuelve el path local o None.
    El archivo se guarda como JPEG independientemente del formato original
    (lo convierte FFmpeg/Pillow al normalizarlo después).
    """
    url = buscar_url_imagen(nombre)
    if not url:
        return None

    Path(carpeta).mkdir(parents=True, exist_ok=True)
    ext = ".jpg"
    if url.lower().endswith(".png"):
        ext = ".png"
    elif url.lower().endswith(".webp"):
        ext = ".webp"

    destino = os.path.join(carpeta, f"wiki_{_slug(nombre)}{ext}")
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            stream=True,
        )
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        if Path(destino).exists() and Path(destino).stat().st_size > 10_000:
            return destino
    except Exception:
        pass
    return None


def descargar_personajes(nombres: list[str], carpeta: str) -> dict[str, str]:
    """
    Descarga las imágenes para una lista de nombres.
    Devuelve dict {nombre: path_local}. Los que no se encuentran se omiten.
    """
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    resultado: dict[str, str] = {}
    for nombre in nombres:
        ruta = descargar_imagen(nombre, carpeta)
        if ruta:
            resultado[nombre] = ruta
    return resultado
