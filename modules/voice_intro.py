"""
Genera una intro de voz corta ("¿Ya viste este video viral?") con ElevenLabs
y la prepone al video: pantalla negra + voz → luego arranca el video.
"""
import os
import random
import subprocess
import tempfile
from pathlib import Path

import requests

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
FFMPEG  = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# Charlie — voz masculina expresiva, buena para español
_VOICE_ID = "IKne3meq5aSn9XLyUdCD"

_FRASES = [
    "Oye, ¿ya viste este video viral?",
    "Mirá esto antes de irte.",
    "Ojo, esto no te lo podés perder.",
    "Dale play, te juro que vale.",
    "Che, esperá un segundo.",
]


def _generar_bytes_audio(frase: str) -> bytes | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": frase,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.3,
            "similarity_boost": 0.75,
            "style": 0.8,
            "use_speaker_boost": True,
        },
    }
    try:
        r = requests.post(
            f"{ELEVENLABS_API_BASE}/text-to-speech/{_VOICE_ID}",
            json=payload,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def generar_audio_intro(frase: str | None = None) -> str | None:
    """
    Genera un MP3 corto con la frase de intro, aplica pitch-shift de chipmunk
    y retorna la ruta al archivo temporal. Retorna None si falla.
    """
    texto = frase or random.choice(_FRASES)
    audio_bytes = _generar_bytes_audio(texto)
    if not audio_bytes:
        return None

    tmp_raw = tempfile.mktemp(suffix="_intro_raw.mp3")
    Path(tmp_raw).write_bytes(audio_bytes)

    tmp_shifted = tempfile.mktemp(suffix="_intro.mp3")
    r = subprocess.run(
        [
            FFMPEG, "-y",
            "-i", tmp_raw,
            "-af", "asetrate=44100*1.35,aresample=44100",
            "-c:a", "libmp3lame", "-b:a", "128k",
            tmp_shifted,
        ],
        capture_output=True, timeout=30,
    )
    Path(tmp_raw).unlink(missing_ok=True)

    if r.returncode == 0 and Path(tmp_shifted).exists():
        return tmp_shifted
    return None


def _get_audio_duration(path: str) -> float:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 2.5


def _get_video_dims(path: str) -> tuple[int, int]:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1080, 1920


def agregar_intro_voz(video_in: str, audio_intro: str, video_out: str) -> tuple[bool, str]:
    """
    Prepone la voz sobre pantalla negra antes del video.
    Resultado: [pantalla negra + voz] → [video original].
    La duración del segmento negro = duración de la voz + 0.3s de pausa.
    """
    voice_dur = _get_audio_duration(audio_intro) + 0.3
    w, h = _get_video_dims(video_in)

    tmp_intro = tempfile.mktemp(suffix="_intro_seg.mp4")

    # Paso 1: crear segmento negro con la voz encima
    r1 = subprocess.run([
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"color=black:size={w}x{h}:rate=30",
        "-i", audio_intro,
        "-t", str(voice_dur),
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        tmp_intro,
    ], capture_output=True, timeout=60)

    if r1.returncode != 0:
        err = r1.stderr.decode(errors="replace")[-300:]
        return False, f"Intro segment error: {err}"

    # Paso 2: concatenar [negro+voz] + [video original]
    # scale asegura que ambos segmentos tengan las mismas dimensiones
    r2 = subprocess.run([
        FFMPEG, "-y",
        "-i", tmp_intro,
        "-i", video_in,
        "-filter_complex",
        (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=disable[v0];"
            f"[1:v]scale={w}:{h}:force_original_aspect_ratio=disable[v1];"
            "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        video_out,
    ], capture_output=True, timeout=300)

    Path(tmp_intro).unlink(missing_ok=True)

    if r2.returncode == 0 and Path(video_out).exists() and Path(video_out).stat().st_size > 10_000:
        return True, ""
    err = r2.stderr.decode(errors="replace")[-300:]
    return False, err
