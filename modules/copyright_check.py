"""
Detección y limpieza de copyright en clips individuales, ANTES de unirlos al reel final.

Cada clip descargado se analiza por separado contra la base de fingerprinting de
ACRCloud (https://www.acrcloud.com/). Si se detecta un tramo con audio de copyright
conocido, se silencia SOLO ese tramo con ffmpeg — nunca el video entero, salvo que
el análisis en sí falle, en cuyo caso se silencia todo el audio del clip como
medida de seguridad.

Requiere las variables de entorno ACRCLOUD_HOST, ACRCLOUD_ACCESS_KEY y
ACRCLOUD_ACCESS_SECRET (ver .env.example). Si no están configuradas, este paso se
salta por completo y el flujo de compilación sigue exactamente como antes.

Precisión: ACRCloud identifica música por ventanas de muestra (_CHUNK_SEG segundos),
no al segundo exacto dentro de esa ventana. El rango silenciado es el bloque de
_CHUNK_SEG segundos donde hubo match, no un recorte quirúrgico más fino.
"""
import base64
import hashlib
import hmac
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

_CHUNK_SEG = 10.0   # tamaño de cada ventana de análisis, en segundos
_MIN_MUESTRA = 3.0  # ACRCloud necesita un mínimo de audio por muestra


def _acrcloud_configurado() -> bool:
    return bool(
        os.getenv("ACRCLOUD_HOST")
        and os.getenv("ACRCLOUD_ACCESS_KEY")
        and os.getenv("ACRCLOUD_ACCESS_SECRET")
    )


def _duracion(ruta_video: str) -> float | None:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta_video],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def _extraer_muestra_audio(ruta_video: str, inicio: float, dur: float) -> bytes | None:
    """Extrae un fragmento de audio del video como WAV mono 8kHz para enviar a ACRCloud."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        r = subprocess.run(
            [FFMPEG, "-y", "-ss", str(inicio), "-t", str(dur), "-i", ruta_video,
             "-vn", "-ar", "8000", "-ac", "1", "-f", "wav", tmp.name, "-loglevel", "error"],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        data = Path(tmp.name).read_bytes()
        return data if data else None
    except Exception:
        return None
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _acrcloud_identify(sample: bytes) -> dict | None:
    """Consulta la API de identificación de ACRCloud (/v1/identify) para una muestra de audio."""
    host = os.getenv("ACRCLOUD_HOST", "")
    access_key = os.getenv("ACRCLOUD_ACCESS_KEY", "")
    access_secret = os.getenv("ACRCLOUD_ACCESS_SECRET", "")

    http_method = "POST"
    http_uri = "/v1/identify"
    data_type = "audio"
    signature_version = "1"
    timestamp = str(time.time())

    string_to_sign = "\n".join(
        [http_method, http_uri, access_key, data_type, signature_version, timestamp]
    )
    signature = base64.b64encode(
        hmac.new(access_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")

    files = {"sample": ("sample.wav", sample, "audio/wav")}
    data = {
        "access_key": access_key,
        "sample_bytes": str(len(sample)),
        "timestamp": timestamp,
        "signature": signature,
        "data_type": data_type,
        "signature_version": signature_version,
    }
    try:
        resp = requests.post(f"https://{host}/v1/identify", files=files, data=data, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


_UMBRAL_CONFIANZA = 85  # score mínimo (0-100) de ACRCloud para considerar el match como copyright real


def _es_match_copyright(resultado: dict | None) -> bool:
    """True si ACRCloud encontró un match de MÚSICA COMERCIAL (metadata.music) con
    score de confianza >= _UMBRAL_CONFIANZA. Ignora "custom_files", "humming" y
    cualquier otro tipo que no sea música comercial, y descarta matches de baja
    confianza (falsos positivos)."""
    if not resultado:
        return False
    if resultado.get("status", {}).get("code") != 0:
        return False

    metadata = resultado.get("metadata", {})
    hay_copyright = False
    for tipo, matches in metadata.items():
        if not isinstance(matches, list):
            continue
        for match in matches:
            titulo = match.get("title", "?")
            artista = ", ".join(a.get("name", "?") for a in (match.get("artists") or [])) or "?"
            score = match.get("score", 0)
            print(f"[ACRCloud] tipo={tipo} título='{titulo}' artista='{artista}' score={score}")
            if tipo == "music" and score >= _UMBRAL_CONFIANZA:
                hay_copyright = True
    return hay_copyright


def _fusionar_rangos(rangos: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Fusiona ventanas contiguas/solapadas de match en rangos únicos (inicio, fin)."""
    if not rangos:
        return []
    rangos = sorted(rangos)
    fusionados = [rangos[0]]
    for inicio, fin in rangos[1:]:
        u_inicio, u_fin = fusionados[-1]
        if inicio <= u_fin:
            fusionados[-1] = (u_inicio, max(u_fin, fin))
        else:
            fusionados.append((inicio, fin))
    return fusionados


def _detectar_segmentos(ruta_video: str, duracion: float) -> list[tuple[float, float]]:
    """Recorre el audio del clip en ventanas de _CHUNK_SEG segundos y consulta ACRCloud.
    Retorna los rangos (inicio, fin) con match, ya fusionados."""
    rangos: list[tuple[float, float]] = []
    t = 0.0
    while t < duracion:
        dur = min(_CHUNK_SEG, duracion - t)
        if dur < _MIN_MUESTRA:
            break
        muestra = _extraer_muestra_audio(ruta_video, t, dur)
        if muestra:
            resultado = _acrcloud_identify(muestra)
            if _es_match_copyright(resultado):
                rangos.append((t, t + dur))
        t += _CHUNK_SEG
    return _fusionar_rangos(rangos)


def _silenciar_rangos(ruta_video: str, ruta_out: str, rangos: list[tuple[float, float]]) -> bool:
    """Silencia SOLO los rangos indicados. Video intacto (-c:v copy), audio re-codificado."""
    filtros = ",".join(
        f"volume=enable='between(t,{inicio:.2f},{fin:.2f})':volume=0" for inicio, fin in rangos
    )
    try:
        r = subprocess.run(
            [FFMPEG, "-y", "-i", ruta_video,
             "-af", filtros,
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
             ruta_out, "-loglevel", "error"],
            capture_output=True, timeout=120,
        )
        return r.returncode == 0 and Path(ruta_out).exists()
    except Exception:
        return False


def _silenciar_todo(ruta_video: str, ruta_out: str) -> bool:
    """Silencia el audio completo del clip — medida de seguridad si falla el análisis."""
    try:
        r = subprocess.run(
            [FFMPEG, "-y", "-i", ruta_video, "-c:v", "copy", "-an", ruta_out, "-loglevel", "error"],
            capture_output=True, timeout=60,
        )
        return r.returncode == 0 and Path(ruta_out).exists()
    except Exception:
        return False


def _fmt_ts(seg: float) -> str:
    m, s = divmod(int(round(seg)), 60)
    return f"{m}:{s:02d}"


def revisar_y_limpiar_clips(descargados: list[dict], carpeta_tmp: str, notify=None) -> list[dict]:
    """
    Analiza el audio de cada clip descargado contra ACRCloud y silencia los
    segmentos con copyright detectado, ANTES de que el flujo los una en el reel.

    - Si ACRCloud no está configurado (faltan las variables de entorno), no hace
      nada y devuelve `descargados` intacto — el resto del flujo sigue como siempre.
    - Si el análisis de un clip falla (red, ffmpeg, etc.), silencia el audio
      COMPLETO de ese clip como medida de seguridad y lo informa por Telegram.
    - Devuelve la misma lista de clips, con "ruta" apuntando a la versión limpia
      cuando corresponda (el resto de las claves del dict quedan intactas).
    """
    if not _acrcloud_configurado():
        return descargados

    total = len(descargados)
    lineas = [f"🔍 <b>Analizando copyright en {total} clips...</b>"]
    if notify:
        notify("\n".join(lineas))

    resultado: list[dict] = []
    for i, clip in enumerate(descargados, 1):
        ruta = clip.get("ruta")
        clip_out = dict(clip)

        if not ruta or not Path(ruta).exists():
            lineas.append(f"✅ Clip {i} → Limpio")
            resultado.append(clip_out)
            if notify:
                notify("\n".join(lineas))
            continue

        try:
            duracion = _duracion(ruta)
            if duracion is None:
                raise RuntimeError("no se pudo leer la duración del clip")
            rangos = _detectar_segmentos(ruta, duracion)
        except Exception:
            ruta_out = os.path.join(carpeta_tmp, f"clip_{i}_silenciado_total.mp4")
            if _silenciar_todo(ruta, ruta_out):
                clip_out["ruta"] = ruta_out
            lineas.append(f"⚠️ Clip {i} → Error al analizar, silenciando audio completo por seguridad")
            resultado.append(clip_out)
            if notify:
                notify("\n".join(lineas))
            continue

        if not rangos:
            lineas.append(f"✅ Clip {i} → Limpio")
        else:
            ruta_out = os.path.join(carpeta_tmp, f"clip_{i}_limpio.mp4")
            if _silenciar_rangos(ruta, ruta_out, rangos):
                clip_out["ruta"] = ruta_out
            rangos_txt = ", ".join(f"{_fmt_ts(a)}-{_fmt_ts(b)}" for a, b in rangos)
            lineas.append(f"⚠️ Clip {i} → Copyright en {rangos_txt}, silenciando...")

        resultado.append(clip_out)
        if notify:
            notify("\n".join(lineas))

    return resultado
