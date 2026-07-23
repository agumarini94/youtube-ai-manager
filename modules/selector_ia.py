"""
Búsqueda y selección de videos de TikTok para el workflow automático.
Headless — sin Streamlit. Se puede llamar desde el scheduler o el bot.
"""
import json
import logging
import os
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import requests

logger = logging.getLogger(__name__)

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
    "🔥 Tendencias fútbol":         "general",
    "⚽ Goles épicos":              "goles",
    "🏆 Mundial 2026":             "mundial2026",
    "🌟 Jugadas de crack":          "cracks",
    "😂 Fails de fútbol":           "futbolfails",
    "🥅 Atajadas increíbles":       "atajadas",
    "🇦🇷 Fútbol argentino":        "futbolargentino",
    "🔥 Highlights":                "footballhighlights",
    "⚡ Fútbol callejero":          "freestylesoccer",
    "💥 Momentos épicos":           "futbolmoments",
    "🤣 Reacciones de hinchas":     "fansreactions",
    "🏅 Champions League":          "championsleague",
    "👦 Jóvenes talentos":          "youngtalents",
    "🎯 Penales":                   "penales",
    "🇧🇷 Fútbol brasileño":        "futbolbrasil",
    "🏟️ Ambientes de estadio":     "estadio",
    "🌍 Selecciones del mundo":     "selecciones",
    "💪 Entrenamiento de jugadores":"futboltraining",
}

# Subcategorías por categoría.
# Valor: string (solo tag) o tuple (tag, pista_para_claude).
# La pista le dice a Claude qué tipo de video priorizar/descartar al seleccionar.
SUBCATEGORIAS: dict[str, dict[str, str | tuple]] = {
    "🏆 Mundial 2026": {
        "Todos": "mundial2026",
        "📅 Partidos recientes": (
            "mundial 2026 partido hoy resultado",
            "Preferir clips de partidos jugados recientemente: goles del día, resúmenes de encuentros recientes del mundial, reacciones a resultados. Rechazar análisis pre-torneo o contenido de archivo anterior al torneo.",
        ),
        "⚽ Goles": (
            "mundial2026 goles",
            "Preferir goles con reacción de hinchada o jugadas espectaculares. Rechazar análisis de periodistas o comentarios de escritorio.",
        ),
        "👟 Patadas / Faltas": (
            "mundial2026 faltas",
            "Preferir patadas duras, faltas fuertes, tarjetas rojas y entradas violentas. Rechazar análisis tácticos o resúmenes de partido.",
        ),
        "🎵 Canciones / Himnos": (
            "hinchadas cantando mundial 2026",
            "SOLO videos de hinchas, tribunas o aficionados cantando en estadios, plazas o calles. Rechazar videoclips musicales de artistas, cantantes o bandas.",
        ),
        "😂 Fails": (
            "mundial2026 fails",
            "Preferir errores graciosos, tropiezos, resbalar y situaciones vergonzosas de jugadores o árbitros. Rechazar goles o jugadas buenas.",
        ),
        "🎉 Celebraciones": (
            "mundial2026 celebraciones",
            "Preferir festejos de gol con emoción extrema: llanto, saltos, abrazos, reacciones de hinchada. Rechazar análisis o rueda de prensa.",
        ),
        "🔮 Pronósticos de hoy": "__pronosticos__",
    },
    "⚽ Goles épicos": {
        "Todos": "goles",
        "💥 Golazos": (
            "golazos futbol",
            "Solo goles espectaculares de larga distancia, tiros libres, voleas o situaciones imposibles. Rechazar goles simples o de penal sin mérito.",
        ),
        "🌀 Chilenas": (
            "chilena gol futbol",
            "SOLO chilenas, bicicletas y overhead kicks. Rechazar cualquier gol que no sea de ese tipo aunque sea muy bueno.",
        ),
        "🆓 Tiros libres": (
            "freekick goal futbol",
            "SOLO goles de tiro libre directo. Rechazar córners, jugadas en movimiento o goles de penal.",
        ),
        "🎯 De volea": (
            "volea gol futbol",
            "SOLO goles de volea o de primera sin que bote. Rechazar goles con control previo.",
        ),
    },
    "🏅 Champions League": {
        "Todos": "championsleague",
        "⚽ Goles CL": (
            "champions league goals",
            "Preferir goles espectaculares de Champions con reacción del estadio y contexto de partido importante.",
        ),
        "😮 Remontadas": (
            "champions league comeback",
            "SOLO remontadas épicas donde un equipo revierte una desventaja grande. Rechazar victorias simples.",
        ),
        "🏆 Finales": (
            "champions league final",
            "SOLO clips de finales de Champions: momentos decisivos, goles, celebraciones del campeón.",
        ),
        "⏱ Último minuto": (
            "champions last minute goal",
            "SOLO goles en el último minuto o en tiempo añadido que cambian el resultado. Rechazar goles en tiempo normal.",
        ),
    },
    "🇦🇷 Fútbol argentino": {
        "Todos": "futbolargentino",
        "🔵🟡 Boca": (
            "bocajuniors",
            "SOLO contenido de Boca Juniors: goles, jugadas, hinchada de la Bombonera. Rechazar cualquier contenido de River u otros clubes.",
        ),
        "🔴⚪ River": (
            "riverplate",
            "SOLO contenido de River Plate: goles, jugadas, hinchada del Monumental. Rechazar cualquier contenido de Boca u otros clubes.",
        ),
        "👑 Selección": (
            "seleccionargentina",
            "SOLO contenido de la Selección Argentina mayor: goles, jugadas, festejo de hinchada. Rechazar clubes argentinos.",
        ),
        "⚽ Primera División": (
            "ligaargentina futbol",
            "Preferir goles y jugadas de la Liga Profesional Argentina. Rechazar selecciones nacionales o torneos internacionales.",
        ),
    },
    "😂 Fails de fútbol": {
        "Todos": "futbolfails",
        "❌ Penales fallados": (
            "penalty miss football",
            "SOLO penales que se fallan: afuera, al palo o desviados. Rechazar penales convertidos o atajados.",
        ),
        "🤣 Errores increíbles": (
            "futbol fails graciosos",
            "Errores garrafales, tropiezos, caídas y situaciones ridículas de jugadores. Preferir clips con reacción graciosa. Rechazar lesiones serias.",
        ),
        "🥅 Goles en contra": (
            "futbol goles en contra",
            "SOLO autogoles y goles en contra. Rechazar goles normales aunque sean espectaculares.",
        ),
        "🤦 Remates desviados": (
            "remates fallados futbol",
            "Disparos que van muy lejos del arco, al cielo o con dirección absurda. Preferir los más exagerados y graciosos.",
        ),
    },
    "🎯 Penales": {
        "Todos": "penales",
        "✅ Goles de penal": (
            "penalty goal football",
            "Solo penales convertidos con estilo, pausa o reacción llamativa del público.",
        ),
        "❌ Fallados": (
            "penalty miss football",
            "SOLO penales que se fallan: afuera o al palo. Rechazar penales atajados o convertidos.",
        ),
        "🧤 Atajados": (
            "penalty saved goalkeeper",
            "SOLO atajadas de arquero en penales. Rechazar penales fallados por el pateador o convertidos.",
        ),
        "🔥 Definiciones": (
            "penalty shootout football",
            "SOLO tandas de penales completas o momentos decisivos de shootout en torneos importantes. Rechazar penales aislados.",
        ),
    },
    "🌍 Selecciones del mundo": {
        "Todos": "selecciones",
        "🇦🇷 Argentina": (
            "seleccion argentina futbol",
            "SOLO contenido de la Selección Argentina. Rechazar clubes argentinos.",
        ),
        "🇧🇷 Brasil": (
            "seleccion brasil futbol",
            "SOLO contenido de la Selección Brasileña. Rechazar clubes brasileños.",
        ),
        "🇫🇷 Francia": (
            "equipe de france football",
            "SOLO contenido de la Selección Francesa. Rechazar clubes franceses.",
        ),
        "🇪🇸 España": (
            "seleccion espana futbol",
            "SOLO contenido de la Selección Española. Rechazar clubes españoles.",
        ),
    },
}

# Tercer nivel: sub-subcategorías por subcategoría específica.
# Estructura: {categoria: {subcategoria: {sub_sub: valor}}}
SUB_SUBCATEGORIAS: dict[str, dict[str, dict[str, str | tuple]]] = {
    "🏆 Mundial 2026": {
        "📅 Partidos recientes": {
            "Todos": (
                "mundial 2026 partido hoy resultado",
                "Preferir clips de partidos jugados recientemente. Rechazar análisis pre-torneo o contenido de archivo.",
            ),
            "⚽ Goles": (
                "mundial 2026 goles partido hoy",
                "SOLO goles convertidos en partidos recientes del mundial. Preferir goles con reacción de hinchada. Rechazar análisis o goles de entrenamientos.",
            ),
            "👟 Faltas / Tarjetas": (
                "mundial 2026 faltas tarjeta partido hoy",
                "Preferir faltas fuertes, tarjetas rojas y entradas violentas de partidos recientes. Rechazar resúmenes generales sin jugada específica.",
            ),
            "🎵 Hinchadas": (
                "mundial 2026 hinchadas estadio partido hoy",
                "SOLO videos de hinchas y ambiente en el estadio durante partidos recientes. Rechazar videoclips musicales o contenido fuera del estadio.",
            ),
            "😂 Fails": (
                "mundial 2026 fails error partido hoy",
                "Preferir errores graciosos, tropiezos y situaciones vergonzosas en partidos recientes. Rechazar goles o jugadas buenas.",
            ),
            "🎉 Celebraciones": (
                "mundial 2026 celebracion gol partido hoy",
                "Preferir festejos de gol con emoción extrema en partidos recientes: llanto, saltos, abrazos. Rechazar análisis o rueda de prensa.",
            ),
            "📋 Resúmenes": (
                "mundial 2026 resumen partido hoy highlights",
                "Preferir resúmenes cortos de partidos recientes con los momentos más importantes. Rechazar análisis pre-partido o predicciones.",
            ),
        },
    },
}


def sub_tag(val: str | tuple) -> str:
    """Extrae el tag de búsqueda de un valor de SUBCATEGORIAS."""
    return val[0] if isinstance(val, tuple) else val


def sub_pista(val: str | tuple) -> str | None:
    """Extrae la pista para Claude de un valor de SUBCATEGORIAS (None si no tiene)."""
    return val[1] if isinstance(val, tuple) else None

_TAGS_GENERALES = ["goles", "futbol", "mundial2026", "football", "futbolfails", "cracks", "soccer"]
_HISTORIAL = Path(__file__).parent.parent / "data" / "historial_videos.json"
MIN_VISTAS = 1_000


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

def _info_por_url_instagram(url: str) -> dict | None:
    """Metadata de un reel de Instagram via yt-dlp (sin descargar)."""
    try:
        import yt_dlp
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cookiesfrombrowser": ("chrome",),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url.strip(), download=False)
        vid_id = str(info.get("id") or url.rstrip("/").split("/")[-1])
        return {
            "id": vid_id,
            "titulo": (info.get("title") or info.get("description") or "Reel de Instagram")[:120],
            "canal": info.get("uploader") or info.get("channel") or "",
            "vistas": int(info.get("view_count") or 0),
            "duracion": int(info.get("duration") or 0),
            "thumbnail": info.get("thumbnail") or "",
            "url": url.strip(),
            "plataforma": "instagram",
        }
    except Exception:
        return None


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
            ct = int(item.get("create_time") or 0)
            if not ct and vid_id.isdigit():
                try:
                    ct = int(vid_id) >> 32
                except Exception:
                    ct = 0
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
                "create_time": ct,
            })
        return videos
    except Exception:
        return []


def _buscar_keywords(query: str, cantidad: int = 20) -> list[dict]:
    """Busca videos por palabra clave via tikwm feed/search (complementa hashtags)."""
    try:
        r = requests.post(
            "https://www.tikwm.com/api/feed/search",
            data={"keywords": query, "count": cantidad, "cursor": 0},
            headers=_HEADERS,
            timeout=15,
        )
        data = r.json()
        if data.get("code") != 0:
            return []

        # feed/search puede devolver data como dict con "videos" o directamente como lista
        raw = data.get("data", {})
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("videos") or raw.get("data") or []
        else:
            return []

        videos = []
        for item in items:
            autor = item.get("author") or {}
            uid = autor.get("unique_id") or autor.get("uniqueId") or ""
            vid_id = str(item.get("video_id") or item.get("id") or "")
            if not vid_id:
                continue
            ct = int(item.get("create_time") or item.get("createTime") or 0)
            if not ct and vid_id.isdigit():
                try:
                    ct = int(vid_id) >> 32
                except Exception:
                    ct = 0
            videos.append({
                "id": vid_id,
                "titulo": item.get("title") or item.get("desc") or "Sin título",
                "canal": uid,
                "vistas": int(item.get("play_count") or item.get("playCount") or 0),
                "duracion": int(item.get("duration") or 0),
                "thumbnail": item.get("cover") or item.get("origin_cover") or item.get("originCover") or "",
                "url": f"https://www.tiktok.com/@{uid}/video/{vid_id}",
                "download_url": item.get("play") or "",
                "download_url_wm": item.get("wmplay") or "",
                "create_time": ct,
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


def _descargar_instagram(video: dict, carpeta: str) -> str | None:
    """Descarga un reel de Instagram usando yt-dlp."""
    try:
        import yt_dlp
    except Exception:
        return None
    vid_id = video.get("id", "v")
    outtmpl = os.path.join(carpeta, f"ig_{vid_id}.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "cookiesfrombrowser": ("chrome",),
        "outtmpl": outtmpl,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video["url"]])
    except Exception:
        return None
    for archivo in Path(carpeta).glob(f"ig_{vid_id}.*"):
        if archivo.suffix.lower() in [".mp4", ".webm", ".mkv"]:
            if archivo.stat().st_size > 10_000 and _codec_ok(str(archivo)):
                return str(archivo)
            archivo.unlink(missing_ok=True)
    return None


def _descargar_video(video: dict, carpeta: str) -> str | None:
    if video.get("plataforma") == "instagram":
        return _descargar_instagram(video, carpeta)
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

def _claude_seleccionar(candidatos: list[dict], n: int, pista: str | None = None) -> list[dict]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or not candidatos:
        candidatos.sort(key=lambda x: x.get("vistas", 0), reverse=True)
        return candidatos[:n]

    client = anthropic.Anthropic(api_key=api_key)
    lista = "\n".join(
        f"{i+1}. [{v['duracion']}s | {v['vistas']:,} views | @{v['canal']}] {v['titulo'][:80]}"
        for i, v in enumerate(candidatos)
    )
    pista_txt = f"\nFILTRO DE CONTENIDO (prioritario): {pista}\n" if pista else ""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": (
                f"Seleccioná los mejores {n} videos de TikTok para una compilación de "
                f"fútbol/deportes en YouTube Shorts. "
                f"Priorizá: duración 10-40s (evitar clips muy cortos <6s), altas vistas "
                f"(más vistas = más validado), variedad de creadores (no repetir canal).\n"
                f"{pista_txt}\n"
                f"CRITERIOS DE SELECCIÓN (orden de prioridad):\n"
                f"1. Hook visual potente — algo ocurre de inmediato, sin introducción lenta\n"
                f"2. Más de 50K vistas — validado por el algoritmo de TikTok\n"
                f"3. Duración ideal 10-35s — clips muy cortos rompen el ritmo\n"
                f"4. Diversidad de creadores y tipos de situación\n\n"
                f"ORDEN CRÍTICO (determina si el video consigue retención):\n"
                f"• Posición 1 (HOOK): el clip MÁS sorprendente o impactante visualmente. "
                f"Sin enganche en 3-5s → swipe y el algoritmo deja de distribuirlo.\n"
                f"• Posiciones intermedias: alternancia de intensidad alta y media.\n"
                f"• Última posición: clip fuerte que recompense ver hasta el final "
                f"(retención al 100% es señal clave para el algoritmo).\n\n"
                f"DESCARTAR: títulos genéricos o vacíos, canales repetidos, "
                f"duración <6s o >50s, vistas muy bajas (<10K).\n\n"
                f"{lista}\n\n"
                f"Respondé SOLO los números EN EL ORDEN EXACTO, separados por coma. Ej: 7,2,15,4"
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


def buscar_hashtags(max_duracion: int = 60, tag: str | None = None, pais: str | None = None, max_horas: int | None = None) -> list[dict]:
    """
    Paso 1: busca candidatos crudos en TikTok. Sin filtros ni Claude.
    tag: hashtag específico (ej. "fails"). None = mezcla aleatoria de tags generales.
         "general" también usa mezcla aleatoria.
    pais: sufijo de país (ej. "argentina"). None = global.
    max_horas: si se define, descarta videos publicados hace más de N horas.
    """
    # Para reels (max_duracion ≤ 30s), buscamos con límite relajado para tener más candidatos.
    # La compilación se encarga de recortar cada clip a max_duracion al normalizar.
    max_dur_busqueda = max_duracion if max_duracion > 30 else 60

    historial = cargar_historial()
    candidatos: list[dict] = []
    ids_vistos: set[str] = set()

    if tag and tag != "general":
        tag_sin_espacios = tag.replace(" ", "")
        if " " not in tag:
            # Término de una sola palabra: buscar también como hashtag challenge
            if pais:
                tags_a_buscar = [f"{tag_sin_espacios}{pais}", tag_sin_espacios, pais]
                cantidad_por_tag = 50
            else:
                tags_a_buscar = [tag_sin_espacios]
                cantidad_por_tag = 80
        else:
            # Términos con espacios no funcionan como hashtag — solo keyword search
            tags_a_buscar = []
            cantidad_por_tag = 0

        # Keyword search: siempre usar texto original (con espacios)
        keywords_a_buscar = [tag, f"{tag} viral", f"best {tag}"]
        if pais:
            keywords_a_buscar.append(f"{tag} {pais}")
    else:
        base = random.sample(_TAGS_GENERALES, min(4, len(_TAGS_GENERALES)))
        tags_a_buscar = (base + [pais]) if pais else base
        cantidad_por_tag = 40
        keywords_a_buscar = list(base[:3])

    # Buscar hashtags y keywords en paralelo
    todas_fuentes = (
        [("hashtag", t, cantidad_por_tag) for t in tags_a_buscar]
        + [("keywords", k, 40) for k in keywords_a_buscar]
    )

    def _fetch(fuente):
        tipo, query, cant = fuente
        if tipo == "hashtag":
            return _buscar_hashtag(query, cantidad=cant)
        return _buscar_keywords(query, cantidad=cant)

    if not todas_fuentes:
        return []

    logger.info(f"buscar_hashtags: tag={tag!r} pais={pais!r} max_dur={max_duracion}s "
                f"(buscando hasta {max_dur_busqueda}s) | {len(todas_fuentes)} fuentes")

    with ThreadPoolExecutor(max_workers=min(len(todas_fuentes), 8)) as ex:
        resultados = list(ex.map(_fetch, todas_fuentes))

    total_crudos = sum(len(r) for r in resultados)
    descartados_historial = descartados_dur = descartados_vistas = descartados_antiguos = 0
    limite_ts = (datetime.now() - timedelta(hours=max_horas)).timestamp() if max_horas else None

    for videos_lista in resultados:
        for v in videos_lista:
            if not v["id"] or v["id"] in ids_vistos:
                continue
            if v["id"] in historial:
                descartados_historial += 1
                ids_vistos.add(v["id"])
                continue
            if not (3 <= v.get("duracion", 0) <= max_dur_busqueda):
                descartados_dur += 1
                ids_vistos.add(v["id"])
                continue
            if v.get("vistas", 0) < MIN_VISTAS:
                descartados_vistas += 1
                ids_vistos.add(v["id"])
                continue
            if limite_ts and v.get("create_time") and v["create_time"] < limite_ts:
                descartados_antiguos += 1
                ids_vistos.add(v["id"])
                continue
            candidatos.append(v)
            ids_vistos.add(v["id"])

    logger.info(
        f"  crudos={total_crudos} → "
        f"historial=-{descartados_historial} "
        f"duración=-{descartados_dur} "
        f"vistas<{MIN_VISTAS}=-{descartados_vistas} "
        + (f"antiguos>{ max_horas}h=-{descartados_antiguos} " if max_horas else "")
        + f"→ candidatos={len(candidatos)}"
    )

    # Ordenar por vistas descendente — los más populares primero para Claude
    candidatos.sort(key=lambda x: x.get("vistas", 0), reverse=True)
    return candidatos


def filtrar_por_seguidores(candidatos: list[dict], max_seguidores: int = 2_000_000) -> list[dict]:
    """Paso 2: elimina videos de cuentas con más de max_seguidores seguidores."""
    return _filtrar_por_seguidores(candidatos, max_seguidores)


def seleccionar_con_claude(candidatos: list[dict], n: int, pista: str | None = None) -> list[dict]:
    """Paso 3: Claude elige los mejores N clips del listado."""
    return _claude_seleccionar(candidatos, n, pista=pista)


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
    """Obtiene metadata de URLs de TikTok o Instagram."""
    clips = []
    for url in urls:
        u = url.strip()
        if "instagram.com" in u.lower():
            info = _info_por_url_instagram(u)
        else:
            info = _info_por_url(u)
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
