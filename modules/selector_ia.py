"""
Búsqueda y selección de videos de TikTok para el workflow automático.
Headless — sin Streamlit. Se puede llamar desde el scheduler o el bot.
"""
import json
import os
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic
import requests

TIKWM = "https://www.tikwm.com/api/"

# Caché de seguidores persistido en disco para sobrevivir reinicios del bot
_CACHE_SEG_FILE = Path(__file__).parent.parent / "data" / "cache_seguidores.json"


def _cargar_cache_seg() -> dict[str, int]:
    if _CACHE_SEG_FILE.exists():
        try:
            return json.loads(_CACHE_SEG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _guardar_cache_seg() -> None:
    try:
        _CACHE_SEG_FILE.parent.mkdir(exist_ok=True)
        _CACHE_SEG_FILE.write_text(
            json.dumps(_cache_seguidores, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


_cache_seguidores: dict[str, int] = _cargar_cache_seg()
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.tiktok.com/",
}

# Categorías disponibles — mismas que en el Feed de TikTok de la app Streamlit
# Valor: hashtag para la API. "general" usa mezcla aleatoria de tags populares.
CATEGORIAS: dict[str, str] = {
    "🔥 Tendencias generales":      "general",
    "😂 Fails y humor":             "fails",
    "🤣 Fails graciosos":           "failsgraciosos",
    "💥 Caídas y tropiezos":        "caidas",
    "🏅 Fails deportivos":          "sportsfails",
    "🎬 TikTok fails":              "tiktokfails",
    "😬 Momentos cringe":           "cringe",
    "🎭 Humor cotidiano":           "humor",
    "🤡 Bromas y pranks":           "prank",
    "🧒 Kids fails":                "kidsfails",
    "👴 Abuelos graciosos":         "abuelosgraciosos",
    "🐶 Animales y fails":          "animalfails",
    "🎉 Fails en fiestas":          "partyfails",
    "🏠 Fails en casa":             "homefails",
    "😅 Momentos de vergüenza":     "embarrassing",
    "🤦 Expectativa vs realidad":   "expectativavsrealidad",
    "🎪 Compilaciones virales":     "compilacion",
    "🐾 Animales":                  "animalesgraciosos",
    "⚽ Deportes":                  "deportes",
    "🎵 Bailes y challenges":       "challenge",
    "😱 Momentos épicos":           "momentosepicos",
    "🍕 Comida":                    "comidaviral",
    "🏋️ Fitness":                  "gym",
}

_TAGS_GENERALES = ["fails", "animalfails", "humor", "prank", "kidsfails", "sportsfails", "funny"]
_HISTORIAL = Path(__file__).parent.parent / "data" / "historial_videos.json"


# ── Historial ──────────────────────────────────────────────────────────────────

def cargar_historial() -> set:
    if _HISTORIAL.exists():
        try:
            with open(_HISTORIAL) as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def guardar_en_historial(ids: list[str]):
    usados = cargar_historial()
    usados.update(ids)
    _HISTORIAL.parent.mkdir(exist_ok=True)
    with open(_HISTORIAL, "w") as f:
        json.dump(list(usados)[-500:], f)


# ── tikwm API ──────────────────────────────────────────────────────────────────

def _info_por_url(url: str) -> dict | None:
    """Obtiene metadata de un video de TikTok por URL via tikwm."""
    try:
        resp = requests.post(TIKWM, data={"url": url.strip(), "hd": "1"}, timeout=15)
        d = resp.json()
        if d.get("code") == 0 and d.get("data"):
            v = d["data"]
            autor = v.get("author") or {}
            return {
                "id": str(v.get("id") or url.split("/")[-1]),
                "titulo": v.get("title") or "Sin título",
                "canal": autor.get("unique_id") or autor.get("nickname") or "",
                "vistas": int(v.get("play_count") or 0),
                "duracion": int(v.get("duration") or 0),
                "thumbnail": v.get("cover") or v.get("origin_cover") or "",
                "url": url.strip(),
                # play = H.264 sin watermark (siempre compatible con FFmpeg)
                # hdplay puede ser BVC2 (codec ByteDance, FFmpeg no lo soporta)
                "download_url": v.get("play") or "",
                "download_url_wm": v.get("wmplay") or "",
            }
    except Exception:
        pass
    return None


def _buscar_hashtag(tag: str, cantidad: int = 20) -> list[dict]:
    """Busca videos por hashtag via tikwm challenge API."""
    try:
        r = requests.post(
            "https://www.tikwm.com/api/challenge/info",
            data={"challenge_name": tag},
            timeout=15,
        )
        data = r.json()
        if data.get("code") != 0:
            return []
        ch = data.get("data", {})
        challenge_id = (
            ch.get("id")
            or ch.get("ch_info", {}).get("challenge", {}).get("id")
        )
        if not challenge_id:
            return []

        r2 = requests.post(
            "https://www.tikwm.com/api/challenge/posts",
            data={"challenge_id": challenge_id, "count": cantidad, "cursor": 0},
            timeout=15,
        )
        data2 = r2.json()
        if data2.get("code") != 0:
            return []

        videos = []
        for item in data2.get("data", {}).get("videos", []):
            autor = item.get("author") or {}
            uid = autor.get("unique_id") or ""
            # challenge/posts usa "video_id", no "id"
            vid_id = str(item.get("video_id") or item.get("id") or "")
            videos.append({
                "id": vid_id,
                "titulo": item.get("title") or "Sin título",
                "canal": uid,
                "vistas": int(item.get("play_count") or 0),
                "duracion": int(item.get("duration") or 0),
                "thumbnail": item.get("cover") or item.get("origin_cover") or "",
                "url": f"https://www.tiktok.com/@{uid}/video/{vid_id}",
                # challenge/posts no tiene hdplay — usar play + wmplay
                "download_url": item.get("play") or "",
                "download_url_wm": item.get("wmplay") or "",
            })
        return videos
    except Exception:
        return []


# ── Descarga ───────────────────────────────────────────────────────────────────

def _codec_ok(ruta: str) -> bool:
    try:
        probe = subprocess.run(
            [FFPROBE, "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,codec_tag_string",
             "-of", "default=noprint_wrappers=1", ruta],
            capture_output=True, text=True, timeout=10,
        )
        if any(k in probe.stdout.lower() for k in ("bvc2", "codec_name=none")):
            return False
        dec = subprocess.run(
            [FFMPEG, "-v", "error", "-i", ruta, "-t", "0.5", "-f", "null", "-"],
            capture_output=True, text=True, timeout=20,
        )
        return not any(k in dec.stderr.lower() for k in ("no decoder found", "decoder found for: none"))
    except Exception:
        return True


def _descargar_video(video: dict, carpeta: str) -> str | None:
    vid_id = video.get("id", "v")
    intentos = [
        (video.get("download_url", ""),    f"{vid_id}.mp4"),     # sin watermark — primero
        (video.get("download_url_wm", ""), f"{vid_id}_wm.mp4"),  # con watermark — fallback
    ]
    for url, nombre in intentos:
        if not url:
            continue
        ruta = os.path.join(carpeta, nombre)
        try:
            resp = requests.get(url, stream=True, headers=_HEADERS, timeout=90)
            resp.raise_for_status()
            with open(ruta, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if chunk:
                        f.write(chunk)
            if Path(ruta).stat().st_size > 10_000 and _codec_ok(ruta):
                return ruta
            Path(ruta).unlink(missing_ok=True)
        except Exception:
            pass
    return None


# ── Selección con Claude ───────────────────────────────────────────────────────

def _claude_seleccionar(candidatos: list[dict], n: int) -> list[dict]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not candidatos:
        candidatos.sort(key=lambda x: x.get("vistas", 0), reverse=True)
        return candidatos[:n]

    client = anthropic.Anthropic(api_key=api_key)
    lista = "\n".join(
        f"{i+1}. [{v['duracion']}s | {v['vistas']:,} views | @{v['canal']}] {v['titulo'][:80]}"
        for i, v in enumerate(candidatos)
    )
    try:
        resp = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Seleccioná los mejores {n} videos de TikTok para una compilación de "
                f"humor/entretenimiento. Priorizá: duración 8-45s, alto engagement, "
                f"variedad de creadores (no repetir el mismo canal).\n\n"
                f"CRÍTICO — el orden determina si el video consigue vistas o no:\n"
                f"• Posición 1 (HOOK): el clip MÁS sorprendente, gracioso o impactante. "
                f"Si no engancha en los primeros 3-5 segundos, la gente hace swipe y "
                f"YouTube deja de distribuir el video.\n"
                f"• Posiciones intermedias: clips buenos para mantener el ritmo, "
                f"alternando entre intensidad alta y media.\n"
                f"• Última posición: un clip fuerte o gracioso para que la gente "
                f"vea hasta el final (el % de retención al final es clave para el algoritmo).\n\n"
                f"{lista}\n\n"
                f"Respondé SOLO los números EN EL ORDEN EXACTO en que deben aparecer "
                f"en el video, separados por coma. Ej: 7,2,15,4"
            )}],
        )
        indices = [int(x.strip()) - 1 for x in resp.content[0].text.strip().split(",")]
        return [candidatos[i] for i in indices if 0 <= i < len(candidatos)][:n]
    except Exception:
        candidatos.sort(key=lambda x: x.get("vistas", 0), reverse=True)
        return candidatos[:n]


# ── API pública ────────────────────────────────────────────────────────────────

def _obtener_seguidores(unique_id: str) -> int:
    """Consulta la cantidad de seguidores de una cuenta via tikwm. Usa caché."""
    if not unique_id:
        return 0
    if unique_id in _cache_seguidores:
        return _cache_seguidores[unique_id]
    try:
        r = requests.post(
            "https://www.tikwm.com/api/user/info",
            data={"unique_id": unique_id},
            timeout=12,
        )
        data = r.json()
        count = int(data.get("data", {}).get("stats", {}).get("followerCount", 0))
        _cache_seguidores[unique_id] = count
        _guardar_cache_seg()
        return count
    except Exception:
        _cache_seguidores[unique_id] = 0
        return 0


def _filtrar_por_seguidores(candidatos: list[dict], max_seguidores: int) -> list[dict]:
    """
    Elimina videos de cuentas con más de max_seguidores seguidores.
    Consulta los creadores únicos en paralelo (máx 5 workers) para ser rápido.
    """
    unique_ids = list({c["canal"] for c in candidatos if c.get("canal")})
    ids_a_consultar = [uid for uid in unique_ids if uid not in _cache_seguidores]

    if ids_a_consultar:
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_obtener_seguidores, uid): uid for uid in ids_a_consultar}
            for future in as_completed(futures):
                future.result()   # el resultado ya queda en _cache_seguidores

    return [
        c for c in candidatos
        if _cache_seguidores.get(c.get("canal", ""), 0) <= max_seguidores
    ]


def buscar_hashtags(max_duracion: int = 60, tag: str | None = None, pais: str | None = None) -> list[dict]:
    """
    Paso 1: busca candidatos crudos en TikTok. Sin filtros ni Claude.
    tag: hashtag específico (ej. "fails"). None = mezcla aleatoria de tags generales.
         "general" también usa mezcla aleatoria.
    pais: sufijo de país (ej. "argentina"). None = global.
    """
    historial = cargar_historial()
    candidatos: list[dict] = []
    ids_vistos: set[str] = set()

    if tag and tag != "general":
        if pais:
            # Buscar combinación país+categoría, categoría sola y país solo
            tags_a_buscar = [f"{tag}{pais}", tag, pais]
            cantidad_por_tag = 25
        else:
            tags_a_buscar = [tag]
            cantidad_por_tag = 40
    else:
        base = random.sample(_TAGS_GENERALES, min(3, len(_TAGS_GENERALES)))
        tags_a_buscar = (base + [pais]) if pais else base
        cantidad_por_tag = 20

    # Buscar todos los tags en paralelo en vez de secuencialmente
    with ThreadPoolExecutor(max_workers=len(tags_a_buscar)) as ex:
        resultados = ex.map(lambda t: _buscar_hashtag(t, cantidad=cantidad_por_tag), tags_a_buscar)

    for videos_tag in resultados:
        for v in videos_tag:
            if (v["id"] and
                    v["id"] not in historial and
                    v["id"] not in ids_vistos and
                    3 <= v.get("duracion", 0) <= max_duracion):
                candidatos.append(v)
                ids_vistos.add(v["id"])
    return candidatos


def filtrar_por_seguidores(candidatos: list[dict], max_seguidores: int = 2_000_000) -> list[dict]:
    """Paso 2: elimina videos de cuentas con más de max_seguidores seguidores."""
    return _filtrar_por_seguidores(candidatos, max_seguidores)


def seleccionar_con_claude(candidatos: list[dict], n: int) -> list[dict]:
    """Paso 3: Claude elige los mejores N clips del listado."""
    return _claude_seleccionar(candidatos, n)


def buscar_y_seleccionar(n_clips: int = 4, max_duracion: int = 60) -> list[dict]:
    """Pipeline completo: buscar → filtrar seguidores → seleccionar con Claude."""
    candidatos = buscar_hashtags(max_duracion)
    if not candidatos:
        return []
    candidatos = filtrar_por_seguidores(candidatos)
    if not candidatos:
        return []
    return seleccionar_con_claude(candidatos, n_clips)


def info_desde_urls(urls: list[str]) -> list[dict]:
    """Obtiene metadata de URLs de TikTok específicas (para el comando /urls)."""
    clips = []
    for url in urls:
        info = _info_por_url(url.strip())
        if info:
            clips.append(info)
    return clips


def descargar_clips(clips: list[dict], carpeta: str, progress_cb=None) -> list[dict]:
    """Descarga los clips a disco. Retorna los que se descargaron exitosamente."""
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    descargados = []
    total = len(clips)
    for i, v in enumerate(clips, 1):
        if progress_cb:
            titulo = v.get("titulo", "")[:40]
            progress_cb(f"📥 <b>Descargando clip {i}/{total}</b>\n<i>{titulo}</i>")
        ruta = _descargar_video(v, carpeta)
        if ruta:
            clip = dict(v)
            clip["ruta"] = ruta
            descargados.append(clip)
    return descargados
