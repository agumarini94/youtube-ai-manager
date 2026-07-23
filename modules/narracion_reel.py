"""
Módulo — Reel de Narración con Stock Video.
Formato faceless para canales propios: la voz manda, el video se ajusta.

Pipeline:
  1. Claude genera guión (90-130 palabras, ~40-55s), título, hashtags y queries Pexels.
  2. ElevenLabs convierte el guión en MP3 con voz expresiva/graciosa.
  3. Se mide la duración EXACTA del audio.
  4. Pexels Videos baja N clips stock verticales.
  5. FFmpeg arma un video de esa duración exacta (sin silencios de relleno).
  6. Se mezcla la voz (volumen 1.0) con música de fondo opcional (volumen 0.10).

NO toca compilador.py / sync_audio.py / voice_intro.py — todo es código nuevo aislado.
"""

import json
import os
import random
import subprocess
import tempfile
from pathlib import Path

import anthropic
import requests

from modules.marca_agua import aplicar_marca_de_agua

# Usamos ffmpeg-full (con libass + libfreetype) para soportar subtítulos quemados,
# drawtext y zoompan. El ffmpeg del PATH (Homebrew minimal) no incluye libass.
# Si ffmpeg-full no está disponible, caemos al de PATH.
_FFMPEG_FULL  = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
_FFPROBE_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"
FFMPEG  = _FFMPEG_FULL  if Path(_FFMPEG_FULL).exists()  else "/opt/homebrew/bin/ffmpeg"
FFPROBE = _FFPROBE_FULL if Path(_FFPROBE_FULL).exists() else "/opt/homebrew/bin/ffprobe"

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

_VIDEO_W = 1080
_VIDEO_H = 1920
_FPS     = 30

# Charlie — voz masculina expresiva en español. Misma que voice_intro.py.
# Override con ELEVENLABS_VOICE_ID_NARRACION en .env si querés probar otra.
_VOICE_ID_DEFAULT = "IKne3meq5aSn9XLyUdCD"

# Settings para que la voz suene desenfadada / con humor (no documental serio)
_VOICE_SETTINGS = {
    "stability": 0.30,         # bajo = más expresivo y variable
    "similarity_boost": 0.75,
    "style": 0.80,             # alto = más dramático y vivo
    "use_speaker_boost": True,
}

_TEMAS_FILE = Path(__file__).parent.parent / "data" / "temas_narracion_futbol.json"


# ── Carga del banco de temas ──────────────────────────────────────────────────

def cargar_temas() -> dict:
    """Lee data/temas_narracion_futbol.json. Lanza si no existe."""
    if not _TEMAS_FILE.exists():
        raise FileNotFoundError(f"No existe {_TEMAS_FILE}")
    with open(_TEMAS_FILE, encoding="utf-8") as f:
        return json.load(f)


def listar_temas() -> list[dict]:
    """Atajo: devuelve solo la lista de temas."""
    return cargar_temas().get("temas", [])


# ── Generación de guión con Claude ────────────────────────────────────────────

def generar_guion_claude(tema_seed: str, pexels_queries_base: list[str] | None = None) -> dict:
    """
    Pide a Claude el guión completo + SEO + queries Pexels + personajes.

    `tema_seed` es el campo "seed" del JSON de temas o un texto libre del usuario.
    `pexels_queries_base` se le pasa a Claude como sugerencia (puede mantenerlas o cambiarlas).

    Retorna dict:
      {
        "guion": "texto completo, listo para narrar",
        "titulo": "titulo YouTube max 60 chars",
        "descripcion": "descripcion para YouTube",
        "hashtags": ["#Shorts", "#fyp", ...],
        "pexels_queries": [...]  (en inglés, 5-7 términos, NO nombres de personas),
        "personajes": [...]      (lista de nombres propios mencionados, formato Wikipedia)
      }
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Falta ANTHROPIC_API_KEY en .env")

    queries_hint = ""
    if pexels_queries_base:
        queries_hint = (
            "\nQueries Pexels sugeridas (podés mantenerlas o cambiarlas si tu guion va por otro lado): "
            + ", ".join(f'"{q}"' for q in pexels_queries_base)
        )

    prompt = f"""Sos guionista de Shorts virales de fútbol. Estudiás los canales de 2026 que funcionan: Magnates Media, The Why Files, MagicSocco, GoalKings. Tu trabajo NO es informar — es ATRAPAR. Cada guion compite con miles de Shorts por la atención de un hincha que tiene el dedo listo para deslizar.

Tema:
{tema_seed}

═══ REGLAS DURAS ═══

LONGITUD: 95-120 palabras. ESTRICTO. Contá las palabras ANTES de devolver el JSON. Si te pasaste, RECORTÁ — sacando adjetivos y conectores antes de sacar contenido. Más de 120 palabras = el video sale mal sincronizado. NO te excedas "por una idea más".

PRECISIÓN HISTÓRICA — NIVEL DE DETALLE QUE PODÉS USAR:

Tu memoria del fútbol es buena para el panorama general, pero NO para detalles micro. Eso significa:

✅ SÍ podés usar:
- Resultados famosos confirmados (Argentina 3-3 Francia final 2022, ganó por penales)
- Hechos icónicos públicos (Mano de Dios, Mundial 2022, Messi al PSG en 2021)
- Estadísticas redondas (Maradona campeón del 86, Pelé tres Mundiales)
- Trayectoria oficial (Messi 13 años en Barcelona)

⚠️ EVITÁ tirar detalles que requieran precisión micro (alta probabilidad de equivocarte):
- Minuto exacto de un gol histórico
- Resultado puntual de un partido medio random
- Quién jugó contra quién en una fase específica de un Mundial viejo
- Lesiones puntuales de un jugador en un partido específico
- Conferencias de prensa con citas literales

Si necesitás detalle micro para el guion y NO estás 100% seguro, escribilo más general:
- En vez de "Pelé contra Suecia minuto 18" → "Pelé en uno de los primeros partidos del Mundial 70"
- En vez de "Maradona con tobillo fracturado en la final del 86" → "Maradona jugó el 86 con dolencias físicas que no se hicieron públicas"

Mejor un guion genérico-pero-verdadero que uno específico-pero-falso. Los comentarios destrozan canales por errores chicos.

ESTRUCTURA OBLIGATORIA (no negociable):
1. HOOK (1ª oración, máx 12 palabras): una afirmación específica y polémica que genere "¿qué?". NO una pregunta retórica.
2. CONTRAPROMESA (2ª oración): "Antes de explicarte por qué, mirá esto…" o equivalente. Aplaza el reveal.
3. SETUP (oraciones 3-5): UN dato concreto con nombre/año/lugar/partido específico que sostenga el hook.
4. ESCALADA (oraciones 6-9): otro dato MÁS fuerte. Frase corta tipo "Pero esperate" / "Acá se pone raro" / "Ojo con lo que viene". Mini-cliffhanger.
5. REVEAL (oraciones 10-12): el punch line que cumple el hook. Debe sorprender.
6. CTA (1 oración): una pregunta ESPECÍFICA al contenido del guion, no "¿qué pensás?". Ej: "Si fueras técnico, ¿lo sacabas en el segundo tiempo? Dale, te leo." / "¿Te tatuarías esa cábala? Bancame en comentarios."

═══ HOOKS PROHIBIDOS (cliché AI slop, evitar como la peste) ═══
❌ "Lo que voy a contarte parece mentira pero es 100% real"
❌ "Lo que pasó / Lo que viste / Lo que viviste…"
❌ "Si pensabas que X, esperate a escuchar esto"
❌ "Esto que voy a contar / vas a flashear / te va a volar la cabeza"
❌ "Hoy te voy a contar 3 datos / 5 cosas / 7 secretos"
❌ "Prepárate / Agarrate / Sentate"
❌ Cualquier frase con "increíble", "alucinante", "asombroso"

═══ HOOKS QUE SÍ FUNCIONAN (modelo, no copies textual) ═══
✅ "Maradona tenía un contrato secreto con Coca-Cola que vetaba a Pepsi del vestuario."
✅ "Hay un gol del Mundial 82 que la FIFA borró del archivo oficial."
✅ "Messi rechazó 200 millones del Al Hilal por una sola cláusula."
✅ "El árbitro del partido más raro del Mundial 86 desapareció tres días."

Patrón: hecho ultra-específico + tensión inmediata. Sin adjetivos. Sin preámbulo.

═══ ESPECIFICIDAD (regla CRÍTICA) ═══
Cada afirmación factual del guion DEBE tener:
- Un nombre propio (jugador, técnico, club), O
- Un año/fecha concreta, O
- Un partido identificable.

NO escribas "hay jugadores que…" / "muchos equipos…" / "algunos cracks…" — son señales de bot.
SÍ escribas "Riquelme, en el River-Boca del 2004, hizo…"

═══ VERACIDAD (regla INNEGOCIABLE — más importante que la creatividad) ═══

VOS NO PODÉS INVENTAR HECHOS. La gente fact-checkea en los comentarios y un dato falso destroza el canal.

Antes de escribir cada oración, preguntate: "¿esto se puede comprobar en Wikipedia, Transfermarkt o en archivos públicos de partidos?". Si la respuesta es NO, tenés DOS opciones:

OPCIÓN A — Cambiar el ángulo:
Buscá otro dato sobre el mismo tema que SÍ sea verificable y conocido. Hay miles de hechos reales del fútbol; no necesitás inventar.

OPCIÓN B — Enmarcar como leyenda/rumor:
Si vas a contar una anécdota que circula pero NO está documentada, OBLIGATORIO marcarla así:
- "Cuenta la leyenda que…"
- "Dicen los que estaban en el vestuario que…"
- "Circula la versión de que…"
- "Se rumorea desde hace años que…"
- "Según los compañeros de equipo…"

NUNCA presentes como hecho duro algo que es leyenda. NO inventes nombres de personas reales (utileros, médicos, periodistas) atribuyéndoles citas o acciones específicas — eso es difamación y además inverificable.

EJEMPLOS DE QUÉ ESTÁ MAL Y CÓMO ARREGLARLO:

❌ MAL (inventado): "El utilero Hélder Mota declaró que Neves le mandaba mensajes a las 11 de la noche."
✅ BIEN (enmarcado): "Circula la versión de que Neves le mandaba un mensaje al utilero todas las noches."
✅ MEJOR (hecho real): "Después del partido, declaró en conferencia que dormía con los guantes desde los 8 años."

❌ MAL (inventado): "Messi siempre entró al campo con el pie izquierdo, desde 2006."
✅ BIEN (enmarcado): "Dicen sus compañeros que Messi siempre pisa primero con el pie izquierdo."

═══ HECHOS SEGUROS QUE PODÉS USAR (verificables) ═══
- Estadísticas oficiales (goles, partidos jugados, campeonatos)
- Resultados de partidos concretos (Argentina 3 - Francia 3 en final 2022, etc.)
- Transferencias documentadas (Neymar al PSG por 222M en 2017)
- Frases dichas en conferencias de prensa públicas
- Eventos del Mundial documentados en archivos FIFA
- Trayectoria oficial de jugadores

DATOS QUE TENÉS QUE EVITAR (o enmarcar como rumor):
- Cábalas privadas de jugadores específicos
- Conversaciones de vestuario
- Lo que dijo X jugador a Y compañero
- Detalles de la vida privada sin source pública

═══ TONO ═══
- Voz: experto futbolero contándole algo a un amigo, no narrador de documental.
- Frases SHORT. 6-12 palabras máximo. Una idea por frase. Sin subordinadas largas.
- Pattern interrupts: "Pero acá viene lo bueno." / "Esperate." / "Ahora, atento." → cada 3-4 oraciones.
- Lunfardo SUTIL. Máximo 2 modismos en TODO el guion. Si decís "tremendo", NO digas "una locura". Si decís "te lo digo en serio", NO digas "literal". Repetir muletillas = falla.

═══ PROHIBIDO ABSOLUTO ═══
❌ Tono de noticiero / documental.
❌ "Hoy hablaremos de…" / "En este video…"
❌ Moralejas o conclusiones genéricas.
❌ Repetir 2+ veces la misma muletilla.
❌ Inventar nombres, fechas o partidos.
❌ Datos sin nombre propio o sin año.
❌ Hooks de la lista prohibida (ni siquiera parafraseados).
{queries_hint}

QUERIES PEXELS:
6 frases CORTAS en INGLÉS para buscar videos stock verticales (formato 9:16) que ilustren visualmente el guion. Tipo: "soccer crowd celebration", "stadium night", "football trophy". Genéricas y visuales, NO específicas de personas reales (Pexels no tiene eso).

PERSONAJES (nombres propios para Wikipedia):
Lista de hasta 4 nombres propios de PERSONAS reales mencionadas en el guion (futbolistas, técnicos, periodistas). Formato EXACTO como aparece en Wikipedia: "Lionel Messi", "Diego Maradona", "Zlatan Ibrahimović", "Cristiano Ronaldo". Si no hay personas reales mencionadas, devolvé lista vacía. NO incluyas equipos ni torneos.

TÍTULO YOUTUBE:
Máx 60 caracteres. Genera curiosidad. Formatos: "[Tema]: lo que nadie te contó", "El secreto del [X] que jamás imaginaste", "3 datos brutales sobre [X]".

DESCRIPCIÓN:
Una línea con gancho + el guion completo + CTA + hashtags al final.

HASHTAGS:
Fijos: #Shorts #fyp #parati
3-4 temáticos de fútbol/Mundial en español.
Total 6-7.

Respondé SOLO con JSON válido (sin markdown):
{{
  "guion": "texto completo listo para narrar",
  "titulo": "titulo max 60 chars",
  "descripcion": "descripcion completa",
  "hashtags": ["#Shorts", "#fyp", "#parati", "#futbol", "#tag5", "#tag6"],
  "pexels_queries": ["query1", "query2", "query3", "query4", "query5", "query6"],
  "personajes": ["Nombre Apellido", "Otro Nombre"]
}}"""

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Generación de voz con ElevenLabs ──────────────────────────────────────────

def _voice_id() -> str:
    return os.getenv("ELEVENLABS_VOICE_ID_NARRACION") or _VOICE_ID_DEFAULT


def generar_audio_narracion(texto: str) -> str | None:
    """
    Convierte el guión completo en un MP3 con voz expresiva.
    Retorna la ruta al MP3 temporal o None si falla.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None

    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": _VOICE_SETTINGS,
    }
    try:
        r = requests.post(
            f"{ELEVENLABS_API_BASE}/text-to-speech/{_voice_id()}",
            json=payload,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            timeout=120,
        )
        r.raise_for_status()
        audio_bytes = r.content
    except Exception:
        return None

    if not audio_bytes or len(audio_bytes) < 1000:
        return None

    tmp = tempfile.mktemp(suffix="_narracion.mp3")
    Path(tmp).write_bytes(audio_bytes)
    return tmp


# ── Consulta de uso ElevenLabs (para contador "te quedan X videos") ──────────

# Promedio empírico: un guion de ~110 palabras se traduce a ~700 caracteres.
# Lo usamos como divisor para estimar cuántos videos más entran en lo que queda.
_CHARS_PROMEDIO_POR_VIDEO = 700


def obtener_uso_elevenlabs() -> dict | None:
    """
    Consulta el endpoint /v1/user/subscription de ElevenLabs.
    Retorna dict con:
      {
        "chars_usados": int,
        "chars_limite": int,
        "chars_restantes": int,
        "tier": str,
        "videos_restantes_estimados": int,
        "porcentaje_usado": float,
      }
    o None si la API falla.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    try:
        r = requests.get(
            f"{ELEVENLABS_API_BASE}/user/subscription",
            headers={"xi-api-key": api_key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    usados  = int(data.get("character_count", 0) or 0)
    limite  = int(data.get("character_limit", 0) or 0)
    rest    = max(0, limite - usados)
    videos  = rest // _CHARS_PROMEDIO_POR_VIDEO  # división entera = conservador
    pct     = (usados / limite * 100) if limite else 0
    return {
        "chars_usados":               usados,
        "chars_limite":               limite,
        "chars_restantes":            rest,
        "tier":                       (data.get("tier") or "free").lower(),
        "videos_restantes_estimados": int(videos),
        "porcentaje_usado":           round(pct, 1),
    }


def hay_creditos_para_guion(num_palabras: int) -> tuple[bool, str]:
    """
    Decide si hay suficientes caracteres en ElevenLabs para generar un guion
    de ese largo. Devuelve (True/False, mensaje_humano).
    """
    uso = obtener_uso_elevenlabs()
    if uso is None:
        # No pudimos consultar — dejamos pasar (no bloqueamos por un error de red)
        return True, ""
    chars_estimados = max(400, num_palabras * 6)  # ~6 chars promedio por palabra
    if uso["chars_restantes"] < chars_estimados:
        return False, (
            f"⛔ <b>No alcanzan los caracteres de ElevenLabs</b>\n\n"
            f"Necesitás ~{chars_estimados:,} para este guion\n"
            f"Te quedan {uso['chars_restantes']:,} de {uso['chars_limite']:,}\n"
            f"Plan: <b>{uso['tier']}</b>\n\n"
            f"Esperá al reset mensual o mandá <code>/uso</code> para detalles."
        )
    return True, ""


def obtener_duracion_audio(path: str) -> float:
    """Duración del archivo de audio en segundos. 0 si falla."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── Compilación del video ─────────────────────────────────────────────────────

def _es_imagen(path: str) -> bool:
    """True si el archivo es una imagen estática (extensión común)."""
    return Path(path).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _normalizar_clip(entrada: str, salida: str, duracion: float) -> tuple[bool, str]:
    """
    Recodifica un clip stock (video) a 1080×1920 30fps, lo recorta a `duracion` segundos
    y le quita el audio. Salida lista para concatenar con otros normalizados.
    """
    vf = (
        f"scale={_VIDEO_W}:{_VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={_VIDEO_W}:{_VIDEO_H},"
        f"setsar=1,fps={_FPS}"
    )
    cmd = [
        FFMPEG, "-y",
        "-ss", "0", "-i", entrada,
        "-t", str(round(duracion, 3)),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode != 0 or not Path(salida).exists():
        return False, r.stderr.decode(errors="ignore")[-400:]
    return True, ""


def _imagen_a_clip(entrada: str, salida: str, duracion: float) -> tuple[bool, str]:
    """
    Convierte una imagen estática (JPG/PNG/WEBP) en clip MP4 vertical de la
    duración pedida, con un zoom Ken Burns suave (la imagen va creciendo ~10%
    durante el clip — más cinematográfico que estática).
    """
    total_frames = max(2, int(round(duracion * _FPS)))
    # zoompan: zoom progresivo de 1.0 a 1.10 a lo largo del clip
    vf = (
        f"scale=4320:7680:force_original_aspect_ratio=increase,"
        f"crop=4320:7680,"
        f"zoompan=z='min(zoom+0.0005,1.10)':d={total_frames}:s={_VIDEO_W}x{_VIDEO_H}:fps={_FPS},"
        f"setsar=1"
    )
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", entrada,
        "-t", str(round(duracion, 3)),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", "-pix_fmt", "yuv420p",
        "-r", str(_FPS),
        salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode != 0 or not Path(salida).exists():
        return False, r.stderr.decode(errors="ignore")[-400:]
    return True, ""


def _normalizar_segmento(entrada: str, salida: str, duracion: float) -> tuple[bool, str]:
    """Despacha a _imagen_a_clip o _normalizar_clip según el tipo de archivo."""
    if _es_imagen(entrada):
        return _imagen_a_clip(entrada, salida, duracion)
    return _normalizar_clip(entrada, salida, duracion)


def _concatenar_clips(clips_normalizados: list[str], salida: str) -> tuple[bool, str]:
    """Concatena clips ya normalizados con concat demuxer (más estable que filter)."""
    if not clips_normalizados:
        return False, "Sin clips para concatenar."

    lista_path = tempfile.mktemp(suffix="_concat.txt")
    with open(lista_path, "w") as f:
        for c in clips_normalizados:
            f.write(f"file '{c}'\n")

    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0",
        "-i", lista_path,
        "-c", "copy",
        salida,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    Path(lista_path).unlink(missing_ok=True)

    if r.returncode != 0 or not Path(salida).exists():
        # Fallback: re-encode si el copy falló (codec mismatch entre clips)
        cmd_re = [
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", lista_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an", "-pix_fmt", "yuv420p",
            salida,
        ]
        # Volver a escribir lista (la borramos antes)
        with open(lista_path, "w") as f:
            for c in clips_normalizados:
                f.write(f"file '{c}'\n")
        r2 = subprocess.run(cmd_re, capture_output=True, timeout=240)
        Path(lista_path).unlink(missing_ok=True)
        if r2.returncode != 0:
            return False, r2.stderr.decode(errors="ignore")[-400:]
    return True, ""


def _mezclar_voz_musica_y_video(
    video_in: str,
    voz_mp3: str,
    musica: str | None,
    video_out: str,
    dur_total: float,
    vol_musica: float = 0.10,
) -> tuple[bool, str]:
    """
    Combina el slideshow (sin audio) con voz + música opcional.
    La voz manda: el video se corta exactamente a la duración del audio.
    """
    fade_out_start = max(0.0, dur_total - 1.2)

    if musica and Path(musica).exists():
        cmd = [
            FFMPEG, "-y",
            "-i", video_in,
            "-i", voz_mp3,
            "-stream_loop", "-1", "-i", musica,
            "-filter_complex",
            (
                f"[1:a]volume=1.0[voz];"
                f"[2:a]volume={vol_musica},afade=t=out:st={fade_out_start}:d=1.2[mus];"
                "[voz][mus]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map", "0:v", "-map", "[aout]",
            "-t", str(round(dur_total, 3)),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            video_out,
        ]
    else:
        cmd = [
            FFMPEG, "-y",
            "-i", video_in,
            "-i", voz_mp3,
            "-map", "0:v", "-map", "1:a",
            "-t", str(round(dur_total, 3)),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            video_out,
        ]
    r = subprocess.run(cmd, capture_output=True, timeout=240)
    if r.returncode != 0 or not Path(video_out).exists():
        return False, r.stderr.decode(errors="ignore")[-500:]
    return True, ""


def _intercalar_segmentos(imagenes: list[str], videos: list[str]) -> list[str]:
    """
    Mezcla imágenes (personajes) y videos (stock) de forma alternada.
    Empieza con una imagen para que el primer frame del video tenga "cara".
    Si hay más videos que imágenes, los videos restantes quedan al final.
    """
    salida: list[str] = []
    i_img = 0
    i_vid = 0
    # Patrón: imagen, video, imagen, video, ...
    while i_img < len(imagenes) or i_vid < len(videos):
        if i_img < len(imagenes):
            salida.append(imagenes[i_img])
            i_img += 1
        if i_vid < len(videos):
            salida.append(videos[i_vid])
            i_vid += 1
    return salida


def compilar_video_narracion(
    audio_path: str,
    clips_stock: list[str],
    output: str,
    musica: str | None = None,
    vol_musica: float = 0.10,
    imagenes_personajes: list[str] | None = None,
    aplicar_subtitulos: bool = True,
) -> tuple[bool, str]:
    """
    Pipeline completo de compilación SIN bug de sincronización:
      1. Mide duración del audio.
      2. Intercala imágenes de personajes (Wikipedia) con clips stock (Pexels).
      3. Reparte la duración del audio entre todos los segmentos.
      4. Normaliza cada segmento (imágenes → Ken Burns, videos → crop a 9:16).
      5. Concatena, mezcla voz + música, corta con `-shortest`.
      6. Si `aplicar_subtitulos`, quema karaoke por palabra con Whisper.

    Returns: (ok, error_string)
    """
    if not Path(audio_path).exists():
        return False, "Audio de narración no encontrado."

    imagenes_personajes = [p for p in (imagenes_personajes or []) if Path(p).exists()]
    clips_stock         = [c for c in clips_stock if Path(c).exists()]

    if not clips_stock and not imagenes_personajes:
        return False, "No hay segmentos (clips ni imágenes) para usar."

    dur_audio = obtener_duracion_audio(audio_path)
    if dur_audio <= 0:
        return False, "No pude leer la duración del audio."

    # Intercalar fotos de personajes con stock genérico
    segmentos = _intercalar_segmentos(imagenes_personajes, clips_stock)

    n = len(segmentos)
    dur_por_segmento = dur_audio / n
    if dur_por_segmento < 2.0 and n > 1:
        n = max(1, int(dur_audio // 2.0))
        segmentos = segmentos[:n]
        dur_por_segmento = dur_audio / n
    if dur_por_segmento > 10.0:
        dur_por_segmento = 10.0

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_nar_norm_")
    normalizados: list[str] = []
    for i, seg in enumerate(segmentos):
        salida = os.path.join(carpeta_tmp, f"norm_{i:02d}.mp4")
        ok, _err = _normalizar_segmento(seg, salida, dur_por_segmento + 0.4)
        if ok:
            normalizados.append(salida)

    if not normalizados:
        return False, "Ningún segmento pudo normalizarse."

    slideshow = os.path.join(carpeta_tmp, "slideshow.mp4")
    ok, err = _concatenar_clips(normalizados, slideshow)
    if not ok:
        return False, f"Error concatenando segmentos: {err}"

    # Si vamos a quemar subtítulos después, hacemos un mix intermedio
    if aplicar_subtitulos:
        mix_intermedio = os.path.join(carpeta_tmp, "mix_intermedio.mp4")
    else:
        mix_intermedio = output

    ok, err = _mezclar_voz_musica_y_video(
        slideshow, audio_path, musica, mix_intermedio,
        dur_total=dur_audio, vol_musica=vol_musica,
    )
    if not ok:
        return False, f"Error mezclando audio: {err}"

    # Quemar subtítulos karaoke al final
    if aplicar_subtitulos:
        try:
            from modules.subtitles import aplicar_karaoke
            ok_sub, info = aplicar_karaoke(audio_path, mix_intermedio, output, idioma="es")
            if not ok_sub:
                # Si falla, dejamos el video sin subtítulos pero válido
                Path(output).unlink(missing_ok=True)
                Path(mix_intermedio).rename(output)
        except Exception:
            # Cualquier error con whisper/ass — devolver el video sin subs
            if not Path(output).exists():
                Path(mix_intermedio).rename(output)

    # Limpieza de temporales
    for f in normalizados:
        Path(f).unlink(missing_ok=True)
    Path(slideshow).unlink(missing_ok=True)
    if aplicar_subtitulos and Path(mix_intermedio).exists() and mix_intermedio != output:
        Path(mix_intermedio).unlink(missing_ok=True)
    try:
        Path(carpeta_tmp).rmdir()
    except Exception:
        pass

    aplicar_marca_de_agua(output)
    return True, ""


# ── Helpers de SEO ────────────────────────────────────────────────────────────

def construir_seo(datos_ia: dict) -> dict:
    """
    Convierte el dict que devuelve Claude en un seo dict compatible con uploader.py.
    """
    hashtags = datos_ia.get("hashtags", ["#Shorts"])
    descripcion = datos_ia.get("descripcion", "")
    if not descripcion:
        descripcion = datos_ia.get("guion", "")
    if hashtags and "#" in " ".join(hashtags):
        descripcion = descripcion.rstrip() + "\n\n" + " ".join(hashtags)
    return {
        "titulo": (datos_ia.get("titulo") or "Short de fútbol")[:100],
        "descripcion": descripcion,
        "tags": [h.lstrip("#") for h in hashtags],
    }
