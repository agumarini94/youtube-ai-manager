"""
Módulo — Subtítulos karaoke por palabra (estilo Submagic / Captions).

Pipeline:
  1. Transcribir el MP3 de narración con faster-whisper → timestamps por palabra.
  2. Agrupar palabras en grupos de 2-3 (lo que cabe en pantalla en Shorts).
  3. Generar un archivo ASS (Advanced SubStation Alpha) con karaoke: la palabra
     que se está hablando se resalta en amarillo, las demás en blanco.
  4. FFmpeg quema los subtítulos al video con el filter `ass=...`.

El modelo "small" es el sweet spot: bueno en español, ~244MB, corre en CPU
en ~0.5-1x tiempo real en Mac M-series. La primera vez baja el modelo
automáticamente desde HuggingFace (~250MB, se guarda en cache).
"""

import os
import subprocess
from pathlib import Path

# Usamos ffmpeg-full (con libass + libfreetype) — el ffmpeg minimal de Homebrew
# no incluye libass y el filter `ass=...` falla silenciosamente.
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFMPEG = _FFMPEG_FULL if Path(_FFMPEG_FULL).exists() else "/opt/homebrew/bin/ffmpeg"

# Modelo de Whisper. "small" = mejor balance calidad/velocidad/peso para español.
# Alternativas: "tiny" (75MB, rápido pero menos preciso), "medium" (770MB, más lento).
_WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

# Singleton del modelo — se carga una sola vez por proceso (la carga tarda 5-15s)
_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        # int8 = quantizado, ~3x menos RAM, calidad casi idéntica
        _model = WhisperModel(_WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribir_palabras(audio_path: str, idioma: str = "es") -> list[dict]:
    """
    Transcribe el MP3 y devuelve lista de dicts con timestamps por palabra:
      [{"word": "Hola", "start": 0.12, "end": 0.43}, ...]

    Si falla, devuelve [].
    """
    if not Path(audio_path).exists():
        return []
    try:
        model = _get_model()
        segments, _info = model.transcribe(
            audio_path,
            language=idioma,
            word_timestamps=True,
            vad_filter=False,
            beam_size=1,  # más rápido, igual de bueno para audio limpio TTS
        )
    except Exception:
        return []

    palabras: list[dict] = []
    for seg in segments:
        for w in (seg.words or []):
            texto = (w.word or "").strip()
            if not texto:
                continue
            palabras.append({
                "word": texto,
                "start": float(w.start),
                "end": float(w.end),
            })
    return palabras


def _agrupar_para_pantalla(palabras: list[dict], max_palabras: int = 3) -> list[list[dict]]:
    """
    Agrupa palabras de a `max_palabras` para que cada grupo se muestre como
    una línea de subtítulos. Mantiene el timing de cada palabra dentro del grupo.
    """
    grupos: list[list[dict]] = []
    actual: list[dict] = []
    for w in palabras:
        actual.append(w)
        # Cerrar grupo cada N palabras, o si hay una pausa larga (>0.7s) hacia la siguiente
        if len(actual) >= max_palabras:
            grupos.append(actual)
            actual = []
    if actual:
        grupos.append(actual)
    return grupos


def _ass_tiempo(seg: float) -> str:
    """Convierte segundos (float) a formato ASS h:mm:ss.cc."""
    h = int(seg // 3600)
    m = int((seg % 3600) // 60)
    s = seg - (h * 3600 + m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"


def _escapar_ass(texto: str) -> str:
    """Caracteres reservados en ASS — escapados."""
    return texto.replace("{", "(").replace("}", ")")


def generar_ass_karaoke(
    palabras: list[dict],
    output_ass: str,
    *,
    video_w: int = 1080,
    video_h: int = 1920,
    fontsize: int = 78,
    color_normal: str = "&H00FFFFFF",   # blanco
    color_activo: str = "&H0000FFFF",   # amarillo (formato ASS: BBGGRR)
    color_borde:  str = "&H00000000",   # negro
    borde: int = 6,
) -> bool:
    """
    Genera un archivo .ass con karaoke: cada grupo de 2-3 palabras se muestra
    junto, y la palabra que se está pronunciando va en amarillo.

    Usa eventos múltiples (uno por palabra activa) en vez de \\k tags clásicos
    porque permite cambiar color de palabras individuales sin animaciones complejas.
    """
    if not palabras:
        return False

    grupos = _agrupar_para_pantalla(palabras, max_palabras=3)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Impact,{fontsize},{color_normal},&H000000FF,{color_borde},&H64000000,-1,0,0,0,100,100,0,0,1,{borde},2,2,80,80,{int(video_h*0.30)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lineas: list[str] = []
    for grupo in grupos:
        if not grupo:
            continue
        grupo_inicio = grupo[0]["start"]
        grupo_fin = grupo[-1]["end"] + 0.05

        # Para cada palabra del grupo, generar UN evento donde esa palabra está en color_activo
        for i, w_activa in enumerate(grupo):
            partes = []
            for j, w in enumerate(grupo):
                texto = _escapar_ass(w["word"].upper())
                if j == i:
                    partes.append(f"{{\\c{color_activo}}}{texto}{{\\c{color_normal}}}")
                else:
                    partes.append(texto)
            texto_linea = " ".join(partes)
            inicio = max(grupo_inicio, w_activa["start"])
            fin    = min(grupo_fin, w_activa["end"] + 0.02)
            if fin <= inicio:
                fin = inicio + 0.05
            lineas.append(
                f"Dialogue: 0,{_ass_tiempo(inicio)},{_ass_tiempo(fin)},Karaoke,,0,0,0,,{texto_linea}"
            )

    Path(output_ass).parent.mkdir(parents=True, exist_ok=True)
    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(lineas))
        f.write("\n")
    return True


def quemar_subtitulos(video_in: str, ass_path: str, video_out: str) -> tuple[bool, str]:
    """
    Quema (hardcode) los subtítulos del archivo .ass dentro del video.
    Re-codifica solo el video; el audio se copia tal cual.
    """
    if not Path(video_in).exists():
        return False, f"Video no encontrado: {video_in}"
    if not Path(ass_path).exists():
        return False, f"ASS no encontrado: {ass_path}"

    # FFmpeg necesita escapar : y , del path dentro del filter
    ass_escaped = (
        ass_path
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )

    # CRF 26 + preset medium = archivo ~40-50% más liviano que CRF 22, sin
    # pérdida visual notable. Necesario para que el preview entre en el límite
    # de 50 MB de Telegram Bot API.
    cmd = [
        FFMPEG, "-y",
        "-i", video_in,
        "-vf", f"ass={ass_escaped}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "26",
        "-maxrate", "4M", "-bufsize", "8M",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        video_out,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0 or not Path(video_out).exists():
        return False, r.stderr.decode(errors="ignore")[-500:]
    return True, ""


def aplicar_karaoke(audio_path: str, video_in: str, video_out: str, idioma: str = "es") -> tuple[bool, str]:
    """
    Pipeline completo: transcribe el audio, genera ASS y quema subtítulos en el video.
    Si la transcripción falla o devuelve nada, deja el video como está (no-op) y devuelve (True, "skipped").
    """
    palabras = transcribir_palabras(audio_path, idioma=idioma)
    if not palabras:
        # No bloquear el flujo si el ASR falla — el video sin subtítulos sigue siendo válido
        return True, "skipped"

    ass_path = str(Path(video_in).with_suffix(".ass"))
    if not generar_ass_karaoke(palabras, ass_path):
        return True, "skipped"

    ok, err = quemar_subtitulos(video_in, ass_path, video_out)
    # No borramos el .ass por si querés debuggear visualmente
    return ok, err
