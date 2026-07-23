"""
Gestión de música de fondo para compilaciones.
Depositar archivos MP3/M4A en youtube_ai_manager/music/
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

MUSIC_DIR = Path(__file__).parent.parent / "music"
FFMPEG   = "/opt/homebrew/bin/ffmpeg"
YT_DLP   = "/opt/homebrew/bin/yt-dlp"

_EXTENSIONES = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}

# Asegura que /opt/homebrew/bin esté en PATH para que yt-dlp encuentre Deno al resolver JS challenges
_ENV = {**os.environ, "PATH": f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"}


def listar_tracks() -> list[dict]:
    """Lista los tracks disponibles en la carpeta music/."""
    MUSIC_DIR.mkdir(exist_ok=True)
    tracks = []
    for f in sorted(MUSIC_DIR.iterdir()):
        if f.suffix.lower() in _EXTENSIONES:
            tracks.append({"nombre": f.stem, "ruta": str(f)})
    return tracks


def buscar_canciones(nombre: str, n: int = 5) -> list[dict]:
    """
    Busca las primeras N canciones en YouTube sin descargar.
    Retorna lista de {titulo, url, duracion, id}.
    """
    try:
        r = subprocess.run(
            [
                YT_DLP,
                f"ytsearch{n}:{nombre}",
                "--flat-playlist",
                "--dump-json",
                "--no-download",
                "--no-warnings",
                "--quiet",
            ],
            capture_output=True, text=True, timeout=30, env=_ENV,
        )
        results = []
        for line in r.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                vid_id = d.get("id", "")
                dur = int(d.get("duration") or 0)
                url = d.get("url") or d.get("webpage_url") or (
                    f"https://www.youtube.com/watch?v={vid_id}" if vid_id else ""
                )
                if not url:
                    continue
                results.append({
                    "titulo": d.get("title") or "Sin título",
                    "url": url,
                    "duracion": dur,
                    "id": vid_id,
                })
            except Exception:
                continue
        return results
    except Exception:
        return []


def descargar_preview(url: str, segundos: int = 25) -> str | None:
    """
    Descarga los primeros N segundos de audio como preview en /tmp.
    Usa yt-dlp -g para obtener la URL directa y luego FFmpeg descarga solo el tramo.
    Mucho más rápido que descargar el archivo completo.
    """
    try:
        # Obtener URL directa del stream de audio (sin descargar)
        r = subprocess.run(
            [YT_DLP, "-f", "bestaudio", "-g", "--no-warnings", url],
            capture_output=True, text=True, timeout=30, env=_ENV,
        )
        if r.returncode != 0:
            return None
        stream_url = r.stdout.strip().splitlines()[0]
        if not stream_url:
            return None

        # Descargar solo los primeros N segundos via FFmpeg
        tmp = tempfile.mktemp(suffix=".mp3", prefix="ytbot_preview_")
        r2 = subprocess.run(
            [
                FFMPEG,
                "-ss", "0", "-t", str(segundos),
                "-i", stream_url,
                "-c:a", "libmp3lame", "-b:a", "128k",
                "-y", tmp,
            ],
            capture_output=True, timeout=45, env=_ENV,
        )
        if r2.returncode == 0 and Path(tmp).exists() and Path(tmp).stat().st_size > 1_000:
            return tmp
        return None
    except Exception:
        return None


def descargar_desde_url(url: str) -> dict | None:
    """
    Descarga audio completo de una URL de YouTube y lo guarda en music/.
    Retorna {nombre, ruta} o None si falla.
    """
    MUSIC_DIR.mkdir(exist_ok=True)
    antes = set(MUSIC_DIR.glob("*.mp3"))
    try:
        r = subprocess.run(
            [
                YT_DLP, url,
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "192K",
                "-o", str(MUSIC_DIR / "%(title)s.%(ext)s"),
                "--no-playlist",
                "--no-warnings",
            ],
            capture_output=True, text=True, timeout=180, env=_ENV,
        )
        if r.returncode != 0:
            return None
        nuevos = set(MUSIC_DIR.glob("*.mp3")) - antes
        if not nuevos:
            return None
        ruta = str(next(iter(nuevos)))
        return {"nombre": Path(ruta).stem, "ruta": ruta}
    except Exception:
        return None


def buscar_y_descargar(nombre: str) -> dict | None:
    """
    Busca la canción en YouTube por nombre y descarga el primer resultado como MP3 en music/.
    Retorna {nombre, ruta} o None si falla.
    """
    results = buscar_canciones(nombre, n=1)
    if not results:
        return None
    return descargar_desde_url(results[0]["url"])


def sugerir_musica_claude(contexto: str) -> dict | None:
    """
    Claude sugiere una canción apropiada para el video de fútbol.
    contexto: descripción del tipo de video (categoría, tema, formato).
    Retorna {busqueda, razon} o None si falla.
    """
    import anthropic as _anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Sos un editor de videos de fútbol que elige música para YouTube Shorts. "
        f"Elegí UNA canción específica que pegue perfecto para este video:\n\n"
        f"CONTEXTO: {contexto}\n\n"
        f"REGLAS:\n"
        f"• Para compilaciones de goles/highlights épicos → trap, phonk, hip-hop energético\n"
        f"• Para datos curiosos / texto → lo-fi hip-hop, instrumental suave\n"
        f"• Para debate / pregunta viral → beat trap medio, no muy invasivo\n"
        f"• Para historias de hinchas → música emotiva, himno de fútbol o balada\n"
        f"• Para ¿Cuál elegís? → hip-hop animado, pop\n"
        f"• Preferí música sin copyright (lo-fi, NCS, royalty-free) cuando sea posible\n"
        f"• También vale música famosa de fútbol (We Are The Champions, 7 Nation Army, etc.)\n\n"
        f"Respondé SOLO con JSON válido (sin markdown):\n"
        f'{{ "busqueda": "nombre + artista exacto para buscar en YouTube", '
        f'"razon": "por qué esta canción (máx 12 palabras)" }}'
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        import json as _json
        return _json.loads(resp.content[0].text.strip())
    except Exception:
        return None


def mezclar_musica(video: str, musica: str, salida: str, volumen: float = 0.15,
                   reemplazar_audio: bool = False) -> bool:
    """
    Agrega música de fondo a un video preservando el audio original.
    La música se loopea si es más corta que el video.
    volumen: fracción del volumen original del video (0.15 = 15%).
    reemplazar_audio: si True, descarta el audio original y usa solo la música.
    """
    try:
        if reemplazar_audio:
            filter_complex = f"[1:a]volume={volumen}[aout]"
        else:
            filter_complex = (
                f"[1:a]volume={volumen}[bg];"
                "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
        cmd = [
            FFMPEG,
            "-i", video,
            "-stream_loop", "-1", "-i", musica,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
        ]
        if reemplazar_audio:
            cmd += ["-shortest"]
        cmd += ["-y", salida]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        return (
            r.returncode == 0
            and Path(salida).exists()
            and Path(salida).stat().st_size > 10_000
        )
    except Exception:
        return False
