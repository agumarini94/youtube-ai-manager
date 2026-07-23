"""
Módulo — Búsqueda y descarga de videos stock desde Pexels.
Usado por /narracion para conseguir clips verticales (9:16) que se sincronizan
con una narración de voz generada por ElevenLabs.

API: https://www.pexels.com/api/documentation/#videos
"""

import os
import random
from pathlib import Path

import requests

PEXELS_VIDEO_SEARCH = "https://api.pexels.com/videos/search"

_VIDEO_W = 1080
_VIDEO_H = 1920


def _elegir_archivo_vertical(video_files: list[dict]) -> dict | None:
    """
    De los video_files que devuelve Pexels, elige el más adecuado para 9:16.
    Prioriza:
      1. portrait (height > width) y resolución cercana a 1080×1920
      2. landscape de buena resolución (lo croppea ffmpeg después)
    Devuelve el dict del video_file elegido o None.
    """
    if not video_files:
        return None

    portrait = [
        vf for vf in video_files
        if vf.get("width") and vf.get("height") and vf["height"] > vf["width"]
    ]
    candidatos = portrait if portrait else video_files

    def _score(vf: dict) -> int:
        w = vf.get("width", 0)
        h = vf.get("height", 0)
        # Penalizar resoluciones demasiado bajas o demasiado altas (no necesitamos 4K).
        target = abs(h - _VIDEO_H) + abs(w - _VIDEO_W)
        return target

    candidatos = sorted(candidatos, key=_score)
    return candidatos[0] if candidatos else None


def buscar_videos(query: str, per_page: int = 5) -> list[dict]:
    """
    Busca videos en Pexels por query. Retorna lista de dicts con 'url' (mp4) y 'duration'.
    Si no hay PEXELS_API_KEY o falla la búsqueda, retorna [].
    """
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return []
    try:
        resp = requests.get(
            PEXELS_VIDEO_SEARCH,
            headers={"Authorization": api_key},
            params={
                "query": query,
                "per_page": per_page,
                "orientation": "portrait",
                "size": "medium",
            },
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return []

    data = resp.json() or {}
    videos = data.get("videos", []) or []
    salida: list[dict] = []
    for v in videos:
        vf = _elegir_archivo_vertical(v.get("video_files", []) or [])
        if not vf:
            continue
        url = vf.get("link")
        if not url:
            continue
        salida.append({
            "url": url,
            "duration": float(v.get("duration", 0) or 0),
            "width": vf.get("width"),
            "height": vf.get("height"),
        })
    return salida


def descargar_video(url: str, destino: str, timeout: int = 60) -> bool:
    """Descarga un video y lo guarda en `destino`. True si ok."""
    try:
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        with open(destino, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        return Path(destino).exists() and Path(destino).stat().st_size > 50_000
    except Exception:
        return False


def descargar_para_narracion(
    queries: list[str],
    carpeta: str,
    n_videos: int = 6,
    duracion_min: float = 4.0,
) -> list[str]:
    """
    Descarga N videos stock para usar en una narración.

    - Itera sobre `queries` y por cada uno baja 1-2 videos largos (>= duracion_min).
    - Si una query no devuelve resultados, sigue con la siguiente.
    - Si después de recorrer todas las queries faltan videos, repite con las que sí dieron resultado.
    - Devuelve lista de paths a los MP4 descargados.
    """
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    descargados: list[str] = []
    usados: set[str] = set()
    queries_validas: list[str] = []

    # Primera pasada — 1 video por query
    for q in queries:
        if len(descargados) >= n_videos:
            break
        candidatos = buscar_videos(q, per_page=5)
        candidatos = [c for c in candidatos if c["duration"] >= duracion_min]
        if not candidatos:
            continue
        queries_validas.append(q)
        elegido = candidatos[0]
        url = elegido["url"]
        if url in usados:
            continue
        dest = os.path.join(carpeta, f"nar_clip_{len(descargados):02d}.mp4")
        if descargar_video(url, dest):
            descargados.append(dest)
            usados.add(url)

    # Segunda pasada — si faltan, agarrar de las queries que sí funcionaron, con per_page más amplio
    if len(descargados) < n_videos and queries_validas:
        for q in queries_validas:
            if len(descargados) >= n_videos:
                break
            candidatos = buscar_videos(q, per_page=10)
            candidatos = [c for c in candidatos if c["duration"] >= duracion_min and c["url"] not in usados]
            if not candidatos:
                continue
            random.shuffle(candidatos)
            elegido = candidatos[0]
            dest = os.path.join(carpeta, f"nar_clip_{len(descargados):02d}.mp4")
            if descargar_video(elegido["url"], dest):
                descargados.append(dest)
                usados.add(elegido["url"])

    return descargados
