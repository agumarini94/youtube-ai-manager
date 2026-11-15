"""
Orquestador del workflow de automatización.
Headless — sin Streamlit. Llama selector → compilador → SEO → YouTube.
"""
import base64
import json
import os
import random
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import pytz

from modules.selector_ia import descargar_clips, guardar_en_historial, buscar_videos_autopilot
from modules.compilador import concatenar_clips, agregar_overlay_suscripcion
from modules.marca_agua import aplicar_marca_de_agua
from modules.seo_gen import generar_seo, extraer_frames_thumbnail, generar_capitulos
from modules.copyright_check import revisar_y_limpiar_clips

_TZ_ARG = pytz.timezone("America/Argentina/Buenos_Aires")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Hashtags combinados al azar: Columna A (jugador/equipo/genérico) + Columna B (ángulo),
# sin espacio — ej. "messigoals", "cr7skills".
_HASHTAG_COLUMNA_A = [
    "messi", "cr7", "r9", "ronaldinho", "robertocarlos", "zidane", "neymar",
    "maradona", "mbappe", "football", "futbol",
]
_HASHTAG_COLUMNA_B = [
    "core", "goals", "skills", "fails", "moments", "highlights", "edit",
    "compilation", "clips", "reels",
]
_TODAS_LAS_COMBINACIONES = [a + b for a in _HASHTAG_COLUMNA_A for b in _HASHTAG_COLUMNA_B]

_TEMAS_USADOS_FILE = Path(__file__).parent.parent / "data" / "temas_usados.json"


def _cargar_temas_usados() -> list[str]:
    """
    Lee las combinaciones ya usadas desde data/temas_usados.json, descartando las
    que ya no existen en las columnas actuales. Así, al cambiar el nicho, los temas
    de la lista vieja no ocupan lugar ni adelantan el reinicio.
    """
    try:
        with open(_TEMAS_USADOS_FILE) as f:
            usados = json.load(f)
    except Exception:
        return []
    vigentes = set(_TODAS_LAS_COMBINACIONES)
    return [t for t in usados if t in vigentes]


def _marcar_tema_usado(tema: str) -> None:
    """Guarda `tema` como usado. Al agotarse todas las combinaciones, reinicia la lista."""
    usados = _cargar_temas_usados()
    if tema not in usados:
        usados.append(tema)
    if len(usados) >= len(_TODAS_LAS_COMBINACIONES):
        usados = []
    _TEMAS_USADOS_FILE.parent.mkdir(exist_ok=True)
    with open(_TEMAS_USADOS_FILE, "w") as f:
        json.dump(usados, f)


def _generar_tema_aleatorio(excluir: set[str] | None = None) -> str:
    """
    Elige al azar un hashtag combinado (Columna A + Columna B) que no esté usado
    en el historial ni en `excluir` (combinaciones ya probadas en esta misma corrida).
    """
    usados = set(_cargar_temas_usados()) | (excluir or set())
    disponibles = [c for c in _TODAS_LAS_COMBINACIONES if c not in usados]
    if not disponibles:
        # Se agotó el historial global y/o los excluidos de esta corrida: priorizar
        # no repetir dentro de la corrida actual antes que el historial.
        disponibles = [c for c in _TODAS_LAS_COMBINACIONES if c not in (excluir or set())]
    if not disponibles:
        disponibles = _TODAS_LAS_COMBINACIONES
    return random.choice(disponibles)

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# El ffmpeg de PATH (Homebrew minimal) NO trae 'drawtext' compilado. Para quemar el
# texto de gancho del autopilot usamos ffmpeg-full, igual que hace narracion_reel.py.
# Si no está disponible, _quemar_gancho() lo detecta y saltea el paso.
_FFMPEG_FULL = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
_FFMPEG_TEXTO = _FFMPEG_FULL if Path(_FFMPEG_FULL).exists() else FFMPEG
_FUENTE_GANCHO = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

_RESOLUCIONES = {
    "video": (1920, 1080),   # YouTube horizontal
    "reel":  (1080, 1920),   # Vertical — Shorts / Reels
    "ambos": (1080, 1920),   # Igual que reel, pero también compila horizontal
}


def _encontrar_momento_hook(ruta_video: str) -> int | None:
    """
    Usa Claude Vision para identificar el frame más impactante del video.
    Retorna el timestamp en segundos del momento más hook-worthy, o None si falla.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta_video],
            capture_output=True, text=True, timeout=15,
        )
        duracion = float(probe.stdout.strip() or "60")
        if duracion < 10:
            return None

        # Extraer hasta 10 frames distribuidos a lo largo del video
        n_frames = min(10, max(3, int(duracion / 5)))
        intervalo = duracion / (n_frames + 1)
        timestamps = [round(intervalo * (i + 1)) for i in range(n_frames)]

        carpeta_frames = tempfile.mkdtemp()
        frames_info: list[tuple[int, str]] = []
        for ts in timestamps:
            ruta_frame = os.path.join(carpeta_frames, f"hf_{ts:03d}.jpg")
            r = subprocess.run(
                [FFMPEG, "-ss", str(ts), "-i", ruta_video,
                 "-frames:v", "1", "-q:v", "5", "-y", ruta_frame, "-loglevel", "error"],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0 and Path(ruta_frame).exists():
                with open(ruta_frame, "rb") as f:
                    b64 = base64.standard_b64encode(f.read()).decode()
                frames_info.append((ts, b64))
                Path(ruta_frame).unlink(missing_ok=True)

        if len(frames_info) < 2:
            return None

        content: list[dict] = [{
            "type": "text",
            "text": (
                f"Estos {len(frames_info)} frames son de una compilación de videos "
                "graciosos/fails de TikTok, capturados a distintos segundos del video:"
            ),
        }]
        for i, (ts, b64) in enumerate(frames_info, 1):
            content.append({"type": "text", "text": f"Frame {i} — segundo {ts}:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
            })
        content.append({
            "type": "text",
            "text": (
                "¿Cuál de estos frames es el más impactante, sorprendente o gracioso? "
                "Ese será el hook del video: lo primero que ve el espectador en los primeros 5 segundos.\n"
                "Respondé SOLO con el número de frame (ejemplo: 3). Sin explicación ni texto extra."
            ),
        })

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": content}],
        )
        frame_num = int(resp.content[0].text.strip())
        if 1 <= frame_num <= len(frames_info):
            return frames_info[frame_num - 1][0]
    except Exception:
        pass
    return None


def _aplicar_hook(ruta_video: str, inicio_seg: int, carpeta: str) -> str:
    """
    Extrae 5 segundos desde inicio_seg y los prepone al inicio del video (teaser hook).
    El espectador ve el momento más impactante primero → alta retención en los primeros 5s.
    Retorna la ruta del nuevo video, o ruta_video si falla.
    """
    try:
        hook_path = os.path.join(carpeta, "hook_teaser.mp4")
        out_path = os.path.join(carpeta, "compilacion_con_hook.mp4")

        r1 = subprocess.run(
            [FFMPEG, "-ss", str(inicio_seg), "-i", ruta_video,
             "-t", "5", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", "-y", hook_path],
            capture_output=True, timeout=30,
        )
        if r1.returncode != 0 or not Path(hook_path).exists():
            return ruta_video

        lista = os.path.join(carpeta, "hook_concat.txt")
        with open(lista, "w") as f:
            f.write(f"file '{hook_path}'\n")
            f.write(f"file '{ruta_video}'\n")

        r2 = subprocess.run(
            [FFMPEG, "-f", "concat", "-safe", "0", "-i", lista,
             "-c:v", "libx264", "-preset", "fast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", "-y", out_path],
            capture_output=True, timeout=120,
        )
        if r2.returncode == 0 and Path(out_path).exists():
            return out_path
    except Exception:
        pass
    return ruta_video


def _aplicar_anticopyright(video_in: str, video_out: str, musica: str | None = None) -> bool:
    """
    Aplica transformaciones para reducir matching del Content ID:
    hflip + zoom 1.1x + speed 1.07x + reemplazo total del audio
    (música elegida, o silencio si no hay).
    """
    vf = (
        "hflip,"
        "scale=trunc(iw*1.1/2)*2:trunc(ih*1.1/2)*2,"
        "crop=trunc(iw/1.1/2)*2:trunc(ih/1.1/2)*2,"
        "setpts=PTS/1.07"
    )
    if musica and Path(musica).exists():
        cmd = [
            FFMPEG, "-i", video_in,
            "-stream_loop", "-1", "-i", musica,
            "-filter_complex", f"[0:v]{vf}[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-y", video_out,
        ]
    else:
        cmd = [
            FFMPEG, "-i", video_in,
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-y", video_out,
        ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        return r.returncode == 0 and Path(video_out).exists()
    except Exception:
        return False


def compilar_y_generar_seo(clips: list[dict], formato: str = "video", progress_cb=None,
                           musica: str | None = None, volumen: float = 0.15,
                           anticopyright: bool = False,
                           reemplazar_audio: bool = False) -> dict:
    """
    Descarga clips, compila y genera SEO.
    Retorna {'ok': bool, 'video': str, 'preview': str, 'seo': dict,
             'video_horizontal': str|None, 'error': str|None}
    Si formato == 'ambos', compila también una versión horizontal 1920×1080.
    progress_cb: callable(str) que recibe mensajes HTML de progreso, o None.
    """
    def notify(txt):
        if progress_cb:
            progress_cb(txt)

    dual = (formato == "ambos")
    total = (6 if dual else 5) + (1 if (musica or anticopyright) else 0) + 1  # +1 overlay suscripción
    paso = 1

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_")
    ancho, alto = _RESOLUCIONES.get(formato, _RESOLUCIONES["video"])

    notify(f"📥 <b>Paso {paso}/{total} — Descargando {len(clips)} clips...</b>")
    paso += 1
    descargados = descargar_clips(clips, carpeta_tmp, progress_cb=progress_cb)
    if len(descargados) < 1:
        return {"ok": False, "error": "No se pudo descargar ningún clip. Verificá las URLs e intentá de nuevo."}

    # Análisis de copyright por clip individual — ANTES de unirlos en el reel.
    # Si ACRCloud no está configurado, esta llamada no hace nada y sigue el flujo normal.
    descargados = revisar_y_limpiar_clips(descargados, carpeta_tmp, notify=notify)

    # Modo Versus: cada clip trae "_vs_lado" (índice de lado). Se recalculan los
    # puntos de corte sobre `descargados` (no sobre `clips`) porque algunas descargas
    # pueden fallar y correr los índices originales.
    puntos_transicion = [
        i for i in range(1, len(descargados))
        if descargados[i].get("_vs_lado") != descargados[i - 1].get("_vs_lado")
    ] if any(c.get("_vs_lado") is not None for c in descargados) else []

    tipo_label = "Short vertical (1080×1920)" if dual else f"{ancho}×{alto}"
    notify(f"✂️ <b>Paso {paso}/{total} — Compilando {len(descargados)} clips ({tipo_label})...</b>\n<i>Esto tarda 1-3 min según el largo</i>")
    paso += 1
    salida = os.path.join(carpeta_tmp, "compilacion.mp4")
    rutas = [c["ruta"] for c in descargados]
    # Para reels/ambos: recortar cada clip a 15s — YouTube Shorts distribuye mejor bajo 60s total
    max_seg_clip = 15 if formato in ("reel", "ambos") else None
    ok, err = concatenar_clips(rutas, salida, ancho, alto, max_seg_por_clip=max_seg_clip,
                               puntos_transicion=puntos_transicion)
    if not ok:
        return {"ok": False, "error": f"Error en compilación: {err}"}

    # Compilar versión horizontal cuando se pidieron ambos formatos
    video_horizontal = None
    if dual:
        notify(f"📺 <b>Paso {paso}/{total} — Compilando versión horizontal (1920×1080) para YouTube Video...</b>")
        paso += 1
        salida_h = os.path.join(carpeta_tmp, "compilacion_horizontal.mp4")
        ok_h, _ = concatenar_clips(rutas, salida_h, 1920, 1080, puntos_transicion=puntos_transicion)
        if ok_h and Path(salida_h).exists():
            video_horizontal = salida_h

    # Teaser hook: Claude encuentra el momento más impactante y lo pone al inicio.
    # Solo para videos largos — en Shorts/Reels no se aplica porque agregar 5s extra
    # puede superar el límite de 60s y YouTube deja de clasificarlo como Short.
    if not dual and formato not in ("reel", "ambos") and not puntos_transicion:
        notify(f"⚡ <b>Paso {paso}/{total} — Claude buscando el mejor hook para los primeros 5 segundos...</b>")
        paso += 1
        try:
            hook_ts = _encontrar_momento_hook(salida)
            if hook_ts and hook_ts > 5:
                salida_con_hook = _aplicar_hook(salida, hook_ts, carpeta_tmp)
                if salida_con_hook != salida:
                    salida = salida_con_hook
        except Exception:
            pass
    else:
        paso += 1  # mantener el conteo de pasos

    notify(f"🔔 <b>Paso {paso}/{total} — Agregando campanita de suscripción...</b>")
    paso += 1
    salida_ov = os.path.join(carpeta_tmp, "compilacion_con_overlay.mp4")
    ok_ov, _ = agregar_overlay_suscripcion(salida, salida_ov)
    if ok_ov:
        salida = salida_ov
    if formato in ("reel", "ambos"):
        aplicar_marca_de_agua(salida)
    if video_horizontal:
        salida_h_ov = os.path.join(carpeta_tmp, "compilacion_horizontal_overlay.mp4")
        ok_h_ov, _ = agregar_overlay_suscripcion(video_horizontal, salida_h_ov)
        if ok_h_ov:
            video_horizontal = salida_h_ov

    if anticopyright:
        notify(f"🛡️ <b>Paso {paso}/{total} — Aplicando anti-copyright (audio + flip + zoom + speed)...</b>")
        paso += 1
        salida_ac = os.path.join(carpeta_tmp, "compilacion_anticopyright.mp4")
        if _aplicar_anticopyright(salida, salida_ac, musica):
            salida = salida_ac
        if video_horizontal:
            salida_h_ac = os.path.join(carpeta_tmp, "compilacion_horizontal_anticopyright.mp4")
            if _aplicar_anticopyright(video_horizontal, salida_h_ac, musica):
                video_horizontal = salida_h_ac
    elif musica and Path(musica).exists():
        from modules.music_manager import mezclar_musica
        nombre_musica = Path(musica).stem
        notify(f"🎵 <b>Paso {paso}/{total} — Mezclando música de fondo: {nombre_musica}...</b>")
        paso += 1
        salida_con_musica = os.path.join(carpeta_tmp, "compilacion_con_musica.mp4")
        if mezclar_musica(salida, musica, salida_con_musica, volumen=volumen, reemplazar_audio=reemplazar_audio):
            salida = salida_con_musica
            if video_horizontal:
                salida_h_musica = os.path.join(carpeta_tmp, "compilacion_horizontal_musica.mp4")
                if mezclar_musica(video_horizontal, musica, salida_h_musica, volumen=volumen, reemplazar_audio=reemplazar_audio):
                    video_horizontal = salida_h_musica

    notify(f"🖼 <b>Paso {paso}/{total} — Generando preview y extrayendo frames...</b>")
    paso += 1
    preview = os.path.join(carpeta_tmp, "preview.mp4")
    _comprimir_preview(salida, preview)
    frames = extraer_frames_thumbnail(salida, cantidad=4)

    notify(f"🤖 <b>Paso {paso}/{total} — Claude analizando el video, generando SEO y thumbnail...</b>")
    titulos = ", ".join(c.get("titulo", "")[:40] for c in descargados)
    tipo_label = "YouTube Short viral" if formato in ("reel", "ambos") else "Compilación viral de humor"
    seo = generar_seo(
        descripcion=f"Compilación de videos virales de TikTok: {titulos}",
        tipo=tipo_label,
        canal_handle="",
        idioma="Español",
        frames_b64=frames if frames else None,
        formato=formato,
        clips=descargados,
    )

    guardar_en_historial([c["id"] for c in descargados])

    # Capítulos solo para videos largos — en reels/shorts no tiene sentido (duran <60s)
    if formato not in ("reel", "ambos"):
        capitulos = generar_capitulos(descargados)
        if capitulos and "descripcion" in seo:
            seo["descripcion"] += f"\n\n📌 CAPÍTULOS\n{capitulos}"

    thumbnail = None
    try:
        from modules.thumbnail_gen import generar_thumbnail_automatico
        thumbnail = generar_thumbnail_automatico(salida, seo, carpeta_tmp)
    except Exception:
        pass

    return {
        "ok": True,
        "video": salida,
        "preview": preview if Path(preview).exists() else salida,
        "seo": seo,
        "clips_descargados": descargados,
        "thumbnail": thumbnail,
        "video_horizontal": video_horizontal,
    }


# Días buenos para publicar: mar(1), mié(2), jue(3), vie(4)
_BUENOS_DIAS = {1, 2, 3, 4}


def _calcular_mejor_horario(ahora) -> tuple[object, str]:
    """
    Retorna (datetime_local, razon) con el mejor momento para publicar.
    Prioriza HOY si es buen día y queda ventana útil (antes de las 21:00).
    Si ya pasó la ventana o hoy es mal día, busca el próximo día bueno.
    """
    dia = ahora.weekday()
    hora = ahora.hour

    if dia in _BUENOS_DIAS and hora < 21:
        # Hoy es buen día y todavía hay ventana disponible
        if hora < 18:
            dt = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
            razon = f"Hoy es {_DIAS_ES[dia]}, un día de alto tráfico. Publicar a las 18:00 AR captura la audiencia de la tarde."
        else:
            # Ya estamos en el horario pico → próxima hora entera
            dt = ahora.replace(hour=hora + 1, minute=0, second=0, microsecond=0)
            razon = f"Ya estamos en el horario pico. Publicar en la próxima hora maximiza la visibilidad inmediata."
        return dt, razon

    # Fuera de ventana o mal día → buscar el próximo día bueno
    for delta in range(1, 8):
        candidato = ahora + timedelta(days=delta)
        if candidato.weekday() in _BUENOS_DIAS:
            dt = candidato.replace(hour=18, minute=0, second=0, microsecond=0)
            nombre = _DIAS_ES[candidato.weekday()]
            razon = f"El {nombre} a las 18:00 AR es uno de los horarios pico de YouTube para audiencia hispanohablante."
            return dt, razon

    # Nunca debería llegar aquí
    dt = (ahora + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    return dt, "Horario pico estándar."


def generar_recomendacion_horario(categoria: str | None = None) -> dict:
    """
    Usa Claude para recomendar el mejor día y hora de publicación en YouTube.
    Retorna dict con: dia_nombre, hora_local, razon, iso_utc, label.
    """
    ahora = datetime.now(_TZ_ARG)
    dia_actual = _DIAS_ES[ahora.weekday()]

    # Calcular la recomendación base (siempre disponible, no depende de Claude)
    dt_base, razon_base = _calcular_mejor_horario(ahora)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            client = anthropic.Anthropic(api_key=api_key)
            prompt = (
                f"Hoy es {dia_actual} {ahora.strftime('%d/%m/%Y')} a las {ahora.strftime('%H:%M')} "
                f"(hora Argentina, GMT-3).\n"
                f"Se va a publicar una compilación de TikTok de "
                f'"{categoria or "humor/entretenimiento"}" en YouTube.\n'
                f"Audiencia: hispanohablante (Argentina, México, España).\n\n"
                f"REGLA PRINCIPAL — seguila al pie de la letra:\n"
                f"Si hoy ({dia_actual}) es martes, miércoles, jueves o viernes "
                f"Y son antes de las 21:00 hora Argentina → recomendá HOY.\n"
                f"  • Si la hora actual es antes de las 18:00 → hora recomendada: 18:00\n"
                f"  • Si la hora actual es 18:00–20:59 → hora recomendada: próxima hora entera "
                f"(ej: si son las 19:15, recomendá 20:00)\n"
                f"Solo recomendá otro día si hoy es lunes, sábado o domingo, "
                f"o si ya pasaron las 21:00.\n\n"
                f"Datos de audiencia en YouTube para esta región:\n"
                f"- Mejores días: martes, miércoles y jueves\n"
                f"- Mejor horario: 18:00–21:00 Argentina\n"
                f"- Viernes también es bueno\n\n"
                f"Respondé SOLO con JSON válido (sin markdown ni texto extra):\n"
                f'{{"dia_nombre":"jueves","fecha_local":"YYYY-MM-DD",'
                f'"hora_local":"18:00","razon":"...breve explicación de por qué este horario..."}}'
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=250,
                messages=[{"role": "user", "content": prompt}],
            )
            data = json.loads(resp.content[0].text.strip())
            dt_local = _TZ_ARG.localize(
                datetime.strptime(f"{data['fecha_local']} {data['hora_local']}", "%Y-%m-%d %H:%M")
            )
            # Rechazar fechas pasadas
            if dt_local <= ahora:
                raise ValueError("fecha pasada")
            dt_utc = dt_local.astimezone(pytz.utc)
            return {
                "dia_nombre": data["dia_nombre"],
                "hora_local": data["hora_local"],
                "razon": data.get("razon", razon_base),
                "iso_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "label": f"{data['dia_nombre'].capitalize()} a las {data['hora_local']}",
            }
        except Exception:
            pass

    # Fallback con la lógica local correcta
    dt_utc = dt_base.astimezone(pytz.utc)
    dia_nombre = _DIAS_ES[dt_base.weekday()]
    hora_label = dt_base.strftime("%H:%M")
    return {
        "dia_nombre": dia_nombre,
        "hora_local": hora_label,
        "razon": razon_base,
        "iso_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": f"{dia_nombre.capitalize()} a las {hora_label}",
    }


def subir_a_youtube(ruta_video: str, seo: dict, fecha_publicacion: str | None = None, thumbnail: str | None = None) -> str | None:
    """Sube el video a YouTube. Retorna la URL o None si falla."""
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None

    from modules import youtube_auth

    if not youtube_auth.hay_token():
        raise RuntimeError("No se encontró token_youtube.json — autenticá desde Streamlit primero.")

    try:
        creds = youtube_auth.cargar_credenciales()
        if not creds:
            raise RuntimeError("Credenciales de YouTube inválidas o expiradas — reautenticá desde Streamlit.")

        youtube = build("youtube", "v3", credentials=creds)
        if fecha_publicacion:
            # Programado: privado hasta la fecha indicada, luego YouTube lo publica solo
            status = {"privacyStatus": "private", "publishAt": fecha_publicacion}
        else:
            status = {"privacyStatus": "public"}

        from modules.historial import bloque_video_anterior
        descripcion_final = seo.get("descripcion", "") + bloque_video_anterior()

        body = {
            "snippet": {
                "title": seo.get("titulo", "Compilación viral"),
                "description": descripcion_final,
                "tags": seo.get("tags", []),
                "categoryId": "23",
                "defaultLanguage": "es",
                "defaultAudioLanguage": "es",
            },
            "status": status,
        }
        req = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(
                ruta_video, chunksize=1024 * 1024, resumable=True, mimetype="video/*"
            ),
        )
        respuesta = None
        while respuesta is None:
            _, respuesta = req.next_chunk()

        video_id = respuesta["id"]

        # Subir thumbnail si existe
        if thumbnail and Path(thumbnail).exists():
            try:
                from googleapiclient.http import MediaFileUpload as _MFU
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=_MFU(thumbnail, mimetype="image/jpeg"),
                ).execute()
            except Exception as e_th:
                print(f"[thumbnail upload warning] {e_th}")

        url_video = f"https://www.youtube.com/watch?v={video_id}"

        # Registrar el título: es lo que después lee _elegir_titulo_autopilot() para no
        # repetirlo. El path de Streamlit ya lo hacía; el headless no, así que ni el
        # autopilot ni el reel del día aparecían en el historial.
        try:
            from modules.historial import guardar_video_youtube
            guardar_video_youtube(url_video, seo.get("titulo", ""))
        except Exception as e_hist:
            print(f"[historial warning] {e_hist}")

        return url_video
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)) from e


def postear_comentario_encuesta(video_id: str, encuesta: dict) -> bool:
    """
    Postea la encuesta como comentario en el video recién subido.
    Retorna True si se posteó correctamente.
    """
    from modules.seo_gen import _formatear_comentario_encuesta
    texto = _formatear_comentario_encuesta(encuesta)
    if not texto:
        return False
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return False

    from modules import youtube_auth

    try:
        creds = youtube_auth.cargar_credenciales()
        if not creds:
            return False
        youtube = build("youtube", "v3", credentials=creds)
        youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": texto}
                    }
                }
            }
        ).execute()
        return True
    except Exception as e:
        print(f"[encuesta] Error posteando comentario: {e}")
        return False


def _comprimir_preview(entrada: str, salida: str, max_mb: int = 45) -> bool:
    """Genera un preview comprimido para enviar por Telegram si el video es muy grande."""
    try:
        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", entrada],
            capture_output=True, text=True, timeout=15,
        )
        duracion = float(probe.stdout.strip() or "300")
        target_bits = max_mb * 1024 * 1024 * 8
        bitrate = max(200_000, int(target_bits / duracion))

        result = subprocess.run([
            FFMPEG, "-i", entrada,
            "-vf", "scale=854:480",
            "-b:v", str(int(bitrate * 0.85)),
            "-b:a", "64k",
            "-y", salida,
        ], capture_output=True, timeout=300)

        return (
            result.returncode == 0
            and Path(salida).exists()
            and Path(salida).stat().st_size < max_mb * 1024 * 1024
        )
    except Exception:
        return False


# ── Autopilot — recorte, orden y gancho ───────────────────────────────────────
# Todo lo que sigue lo usa SOLO ejecutar_autopilot_pipeline(). El flujo manual
# (/trabajar) sigue pasando por compilar_y_generar_seo(), que no se toca.

_AUTOPILOT_TOTAL_OBJETIVO = 30.0   # segundos — punto medio del rango 20-40s
_AUTOPILOT_TRAMO_MIN = 3.0
_AUTOPILOT_TRAMO_MAX = 6.0
# Con 3 clips el tope de 6s da 18s, por debajo del piso de 20s. Solo en ese caso
# se estira a 7s por clip (21s) para no quedar corto.
_AUTOPILOT_TRAMO_MAX_3_CLIPS = 7.0


def _log_autopilot(msg: str) -> None:
    """Un solo lugar donde registrar los pasos nuevos que degradan sin cortar el reel."""
    print(f"[autopilot] {msg}")


# Nombre a mostrar de cada jugador de la Columna A, para que el título lo abra bien
# escrito. Los temas genéricos (football, futbol) no están acá a propósito.
_JUGADOR_POR_TEMA = {
    "messi": "Messi",
    "cr7": "Cristiano Ronaldo",
    "r9": "Ronaldo Nazário",
    "ronaldinho": "Ronaldinho",
    "robertocarlos": "Roberto Carlos",
    "zidane": "Zidane",
    "neymar": "Neymar",
    "maradona": "Maradona",
    "mbappe": "Mbappé",
}


def _jugador_del_tema(tema: str) -> str:
    """
    Jugador detrás del hashtag — ej. "cr7goals" → "Cristiano Ronaldo". Devuelve ""
    para los temas genéricos (footballskills, futbolcore): ahí el jugador lo deduce
    Claude mirando los clips y los frames.
    """
    for clave in sorted(_JUGADOR_POR_TEMA, key=len, reverse=True):
        if tema.startswith(clave):
            return _JUGADOR_POR_TEMA[clave]
    return ""


def _elegir_titulo_autopilot(seo: dict) -> None:
    """
    Pisa seo["titulo"] con el candidato que menos se parece a los últimos 100 títulos
    ya subidos. Si Claude no devolvió candidatos, o si ninguno baja de 60 caracteres,
    deja el título principal intacto — esto nunca corta la subida.
    """
    try:
        candidatos = [
            c.get("titulo", "") for c in seo.get("candidatos", [])
            if isinstance(c, dict)
        ]
        if not candidatos:
            _log_autopilot("El SEO no trajo candidatos de título: se usa el principal")
            return

        from modules.historial import ultimos_titulos
        from modules.seo_gen import elegir_titulo_menos_repetido

        historial = ultimos_titulos(100)
        elegido = elegir_titulo_menos_repetido(candidatos, historial)
        if not elegido:
            _log_autopilot(
                f"Ningún candidato bajó de 60 chars ({candidatos}): se usa el principal"
            )
            return

        _log_autopilot(
            f"Título elegido entre {len(candidatos)} candidatos, comparados contra "
            f"{len(historial)} títulos del historial: {elegido!r}"
        )
        seo["titulo"] = elegido
    except Exception as e:
        _log_autopilot(f"La elección de título falló ({e}), se usa el título principal")


def _duracion_video(ruta: str) -> float:
    """Duración en segundos de `ruta`, o 0.0 si no se puede leer."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def _duracion_tramo_objetivo(n_clips: int) -> float:
    """Segundos por clip para que el reel completo caiga dentro de 20-40s."""
    if n_clips <= 0:
        return _AUTOPILOT_TRAMO_MIN
    tope = _AUTOPILOT_TRAMO_MAX_3_CLIPS if n_clips == 3 else _AUTOPILOT_TRAMO_MAX
    return min(max(_AUTOPILOT_TOTAL_OBJETIVO / n_clips, _AUTOPILOT_TRAMO_MIN), tope)


def _elegir_tramo_clip(ruta: str, duracion_tramo: float) -> tuple[float, float] | None:
    """
    Claude Vision elige el momento más fuerte del clip y devuelve (inicio, duracion)
    del tramo a conservar. None si no se puede decidir.

    Mismo patrón que _encontrar_momento_hook(): se muestrean frames a lo largo del
    clip y Haiku elige el más impactante. A diferencia de la detección de escenas,
    esto entiende QUÉ pasa en el frame (gol, caño, fail) — que es justamente el
    criterio de "mejor tramo", no dónde están los cortes de edición.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    duracion = _duracion_video(ruta)
    if duracion <= duracion_tramo:
        return None

    # Un frame cada ~2s, entre 4 y 10 muestras.
    n_frames = min(10, max(4, int(duracion / 2)))
    intervalo = duracion / (n_frames + 1)
    timestamps = [round(intervalo * (i + 1), 2) for i in range(n_frames)]

    carpeta_frames = tempfile.mkdtemp(prefix="tramo_")
    frames_info: list[tuple[float, str]] = []
    for ts in timestamps:
        ruta_frame = os.path.join(carpeta_frames, f"f_{ts:07.2f}.jpg")
        r = subprocess.run(
            [FFMPEG, "-ss", str(ts), "-i", ruta,
             "-frames:v", "1", "-q:v", "5", "-y", ruta_frame, "-loglevel", "error"],
            capture_output=True, timeout=15,
        )
        if r.returncode == 0 and Path(ruta_frame).exists():
            with open(ruta_frame, "rb") as f:
                frames_info.append((ts, base64.standard_b64encode(f.read()).decode()))
            Path(ruta_frame).unlink(missing_ok=True)

    if len(frames_info) < 2:
        return None

    content: list[dict] = [{
        "type": "text",
        "text": (
            f"Estos {len(frames_info)} frames son de un clip de fútbol de TikTok de "
            f"{duracion:.0f} segundos, tomados en distintos momentos:"
        ),
    }]
    for i, (ts, b64) in enumerate(frames_info, 1):
        content.append({"type": "text", "text": f"Frame {i} — segundo {ts:.1f}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({
        "type": "text",
        "text": (
            f"Voy a recortar este clip a solo {duracion_tramo:.0f} segundos, quedándome "
            f"con su mejor momento. ¿Cuál de estos frames muestra la acción más "
            f"impactante (gol, jugada, festejo, fail)? Evitá frames de introducción, "
            f"de texto en pantalla o de jugadores quietos.\n"
            f"Respondé SOLO con el número de frame (ejemplo: 3). Sin explicación."
        ),
    })

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": content}],
        )
        num = int(resp.content[0].text.strip())
    except Exception as e:
        _log_autopilot(f"Claude no pudo elegir el tramo de {Path(ruta).name}: {e}")
        return None

    if not 1 <= num <= len(frames_info):
        return None

    # Arrancar 1.5s antes del pico para no entrar con la acción ya empezada.
    pico = frames_info[num - 1][0]
    inicio = max(0.0, min(pico - 1.5, duracion - duracion_tramo))
    return round(inicio, 2), duracion_tramo


def _recortar_clips_autopilot(descargados: list[dict], carpeta: str,
                              duracion_tramo: float, notify=None) -> list[dict]:
    """
    Recorta cada clip a su mejor tramo de ~`duracion_tramo` segundos.
    El tramo lo elige Claude Vision; si falla, se usa el tramo central del clip
    (los TikToks suelen arrancar con intro, el centro es mejor apuesta que el inicio).
    Si el recorte de un clip falla, ese clip queda entero y el reel sigue igual.
    """
    total = len(descargados)
    resultado: list[dict] = []
    for i, clip in enumerate(descargados, 1):
        if notify:
            notify(
                f"✂️ <b>Recortando clips a su mejor tramo...</b>\n"
                f"<i>Clip {i}/{total} — buscando el momento más fuerte</i>"
            )
        clip_out = dict(clip)
        ruta = clip.get("ruta")
        if not ruta or not Path(ruta).exists():
            resultado.append(clip_out)
            continue

        duracion = _duracion_video(ruta)
        if duracion <= 0 or duracion <= duracion_tramo + 0.5:
            resultado.append(clip_out)   # ya es suficientemente corto
            continue

        tramo = None
        try:
            tramo = _elegir_tramo_clip(ruta, duracion_tramo)
        except Exception as e:
            _log_autopilot(f"Clip {i}: error eligiendo el tramo ({e})")
        if tramo is None:
            inicio_centro = max(0.0, (duracion - duracion_tramo) / 2)
            tramo = (round(inicio_centro, 2), duracion_tramo)
            _log_autopilot(
                f"Clip {i}: sin elección de Claude, se usa el tramo central "
                f"({inicio_centro:.1f}s)"
            )

        inicio, dur = tramo
        salida = os.path.join(carpeta, f"tramo_{i:02d}.mp4")
        try:
            r = subprocess.run(
                [FFMPEG, "-ss", str(inicio), "-i", ruta, "-t", str(dur),
                 "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "128k",
                 "-avoid_negative_ts", "make_zero",
                 "-y", salida, "-loglevel", "error"],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0 and Path(salida).exists() and _duracion_video(salida) > 0.5:
                clip_out["ruta"] = salida
            else:
                _log_autopilot(f"Clip {i}: ffmpeg no pudo recortar, se usa el clip completo")
        except Exception as e:
            _log_autopilot(f"Clip {i}: error recortando ({e}), se usa el clip completo")
        resultado.append(clip_out)
    return resultado


def _ordenar_clips_autopilot(clips: list[dict]) -> list[dict]:
    """
    Pone primero el clip más impactante. Reutiliza tal cual la función de orden que
    usa /trabajar (_ordenar_clips_con_claude de telegram_bot.py) — con import diferido,
    porque telegram_bot importa este módulo y a nivel de módulo el import sería circular.
    Si falla, devuelve el orden original.
    """
    if len(clips) < 2:
        return clips
    try:
        from modules.telegram_bot import _ordenar_clips_con_claude
        orden = _ordenar_clips_con_claude(clips)
    except Exception as e:
        _log_autopilot(f"No se pudo ordenar con Claude ({e}), se mantiene el orden original")
        return clips
    if not orden or sorted(orden) != list(range(len(clips))):
        _log_autopilot("Claude no devolvió un orden válido, se mantiene el orden original")
        return clips
    _log_autopilot(f"Orden elegido por Claude: {orden}")
    return [clips[i] for i in orden]


def _generar_texto_gancho(clips: list[dict]) -> str | None:
    """
    Texto de gancho de 5-6 palabras como máximo, en español — el mismo idioma con el
    que se genera el SEO del reel (generar_seo se llama siempre con idioma="Español").
    None si Claude falla o devuelve algo inusable.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    titulos = "\n".join(f"- {c.get('titulo', '')[:80]}" for c in clips if c.get("titulo"))
    prompt = (
        "Estos son los clips de un YouTube Short de fútbol, en el orden en que aparecen:\n"
        f"{titulos or '- (sin títulos)'}\n\n"
        "Escribí un texto de gancho para quemar en pantalla durante los primeros "
        "2 segundos del Short.\n"
        "REGLAS:\n"
        "- En español.\n"
        "- Máximo 6 palabras. Mejor 4 o 5.\n"
        "- En MAYÚSCULAS.\n"
        "- Que genere curiosidad o sorpresa, sin clickbait mentiroso.\n"
        "- Sin emojis, sin hashtags, sin comillas y sin punto final.\n"
        "Respondé SOLO con el texto, nada más."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=40,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = resp.content[0].text.strip().strip("\"'").strip()
    except Exception as e:
        _log_autopilot(f"Claude no pudo generar el texto de gancho: {e}")
        return None

    texto = " ".join(texto.split())   # colapsa saltos de línea y espacios dobles
    if not texto or len(texto) > 60:
        _log_autopilot(f"Texto de gancho descartado por formato: {texto[:80]!r}")
        return None
    palabras = texto.split(" ")[:6]   # si se pasó de largo, se recorta en vez de perderlo
    return " ".join(palabras).upper()


def _lineas_gancho(texto: str) -> list[str]:
    """Parte el gancho en 1 o 2 líneas balanceadas, para que entre grande en vertical."""
    if len(texto) <= 14:
        return [texto]
    palabras = texto.split(" ")
    if len(palabras) < 2:
        return [texto]
    corte = min(
        range(1, len(palabras)),
        key=lambda k: abs(len(" ".join(palabras[:k])) - len(" ".join(palabras[k:]))),
    )
    return [" ".join(palabras[:corte]), " ".join(palabras[corte:])]


def _quemar_gancho(ruta_video: str, texto: str, carpeta: str) -> str:
    """
    Quema `texto` en los primeros 2 segundos del reel, grande y centrado en el tercio
    superior. Devuelve la ruta del video nuevo, o `ruta_video` si no se pudo aplicar.

    Posición: centrado horizontalmente, y = 25% del alto. La marca de agua va en la
    esquina superior derecha ocupando ~10% del ancho (unos 120px de alto en 1080×1920),
    así que a esta altura no hay solape.

    Se usa el texto desde un archivo (textfile=) en vez de inline: evita tener que
    escapar comillas, dos puntos y acentos, y soporta las dos líneas sin trucos.
    """
    if _FFMPEG_TEXTO == FFMPEG:
        _log_autopilot("ffmpeg-full no está instalado (no hay drawtext): se saltea el gancho")
        return ruta_video
    if not Path(_FUENTE_GANCHO).exists():
        _log_autopilot(f"No se encontró la fuente {_FUENTE_GANCHO}: se saltea el gancho")
        return ruta_video

    lineas = _lineas_gancho(texto)
    mas_larga = max(len(l) for l in lineas)
    # Arial Black mide ~0.62 × fontsize por carácter; 950px deja margen a los lados.
    fontsize = max(48, min(120, int(950 / (0.62 * mas_larga))))

    ruta_txt = os.path.join(carpeta, "gancho.txt")
    with open(ruta_txt, "w") as f:
        f.write("\n".join(lineas))

    salida = os.path.join(carpeta, "reel_con_gancho.mp4")
    filtro = (
        f"drawtext=fontfile='{_FUENTE_GANCHO}':textfile='{ruta_txt}':expansion=none"
        f":fontcolor=white:fontsize={fontsize}:borderw=8:bordercolor=black@0.9"
        f":x=(w-text_w)/2:y=h*0.25:line_spacing=14"
        f":enable='between(t,0,2)'"
    )
    try:
        r = subprocess.run(
            [_FFMPEG_TEXTO, "-i", ruta_video, "-vf", filtro,
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "copy", "-y", salida, "-loglevel", "error"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0 and Path(salida).exists():
            _log_autopilot(f"Gancho aplicado: {' / '.join(lineas)} ({fontsize}px)")
            return salida
        _log_autopilot(f"drawtext falló ({r.stderr.strip()[:200]}): el reel sigue sin gancho")
    except Exception as e:
        _log_autopilot(f"Error quemando el gancho ({e}): el reel sigue sin gancho")
    return ruta_video


def _compilar_reel_autopilot(clips: list[dict], musica: str | None, notify=None,
                             gancho_fijo: str | None = None,
                             contexto_seo: str | None = None,
                             sin_encuesta: bool = False,
                             titulos_candidatos: bool = False,
                             jugador: str = "") -> dict:
    """
    Compilación del reel para el modo autopilot. Es una variante de
    compilar_y_generar_seo() — que queda intacta para /trabajar — con tres pasos extra
    pensados para retención: recorte de cada clip a su mejor tramo, orden por impacto
    y texto de gancho quemado en los primeros 2 segundos.

    Orden: descarga → limpieza de copyright → recorte → orden → ensamblado
           → campanita → música → gancho → marca de agua → SEO.

    Si falla el recorte, el orden o el gancho se registra en el log y se sigue con el
    comportamiento de siempre para ese paso. Solo un fallo de descarga o de
    concatenación corta el reel.

    gancho_fijo / contexto_seo / sin_encuesta: los usa el reel del día (efemérides),
    que necesita un gancho y un contexto sacados del dataset en vez de inventados por
    Claude.

    titulos_candidatos / jugador: los usa SOLO el autopilot de las 13:00. Con
    titulos_candidatos=True el SEO pide 3 candidatos de título y se publica el que
    menos se parece al historial. El reel del día no los pasa porque su título sale
    del dataset de efemérides.

    Retorna el mismo dict que compilar_y_generar_seo().
    """
    def aviso(txt):
        if notify:
            notify(txt)

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_auto_")
    ancho, alto = _RESOLUCIONES["reel"]
    total = 8

    aviso(f"📥 <b>Paso 1/{total} — Descargando {len(clips)} clips...</b>")
    descargados = descargar_clips(clips, carpeta_tmp, progress_cb=notify)
    if len(descargados) < 1:
        return {"ok": False, "error": "No se pudo descargar ningún clip. Verificá las URLs e intentá de nuevo."}

    # Limpieza de copyright por clip, antes de recortar y de unirlos. Si ACRCloud no
    # está configurado, esta llamada no hace nada y devuelve la lista intacta.
    aviso(f"🔍 <b>Paso 2/{total} — Revisando copyright en {len(descargados)} clips...</b>")
    descargados = revisar_y_limpiar_clips(descargados, carpeta_tmp, notify=notify)

    duracion_tramo = _duracion_tramo_objetivo(len(descargados))
    aviso(
        f"✂️ <b>Paso 3/{total} — Recortando clips a su mejor tramo "
        f"({duracion_tramo:.0f}s cada uno)...</b>"
    )
    try:
        descargados = _recortar_clips_autopilot(
            descargados, carpeta_tmp, duracion_tramo, notify=notify
        )
    except Exception as e:
        _log_autopilot(f"El recorte falló por completo ({e}), se usan los clips enteros")

    aviso(f"🔀 <b>Paso 4/{total} — Claude ordenando los clips por impacto...</b>")
    descargados = _ordenar_clips_autopilot(descargados)

    aviso(
        f"🎬 <b>Paso 5/{total} — Ensamblando {len(descargados)} clips ({ancho}×{alto})...</b>\n"
        f"<i>Esto tarda 1-3 min</i>"
    )
    salida = os.path.join(carpeta_tmp, "compilacion.mp4")
    rutas = [c["ruta"] for c in descargados]
    # Red de seguridad: si el recorte de algún clip falló, este tope evita que un clip
    # de 60s se lleve puesto el objetivo de 20-40s.
    ok, err = concatenar_clips(rutas, salida, ancho, alto,
                               max_seg_por_clip=int(duracion_tramo) + 1)
    if not ok:
        return {"ok": False, "error": f"Error en compilación: {err}"}

    aviso(f"🔔 <b>Paso 6/{total} — Campanita de suscripción y música...</b>")
    salida_ov = os.path.join(carpeta_tmp, "compilacion_con_overlay.mp4")
    ok_ov, _ = agregar_overlay_suscripcion(salida, salida_ov)
    if ok_ov:
        salida = salida_ov
    if musica and Path(musica).exists():
        from modules.music_manager import mezclar_musica
        salida_musica = os.path.join(carpeta_tmp, "compilacion_con_musica.mp4")
        if mezclar_musica(salida, musica, salida_musica, volumen=0.15):
            salida = salida_musica

    aviso(f"🪝 <b>Paso 7/{total} — Agregando gancho...</b>")
    try:
        # gancho_fijo viene del dataset de efemérides: es un dato, no texto inventado.
        texto_gancho = gancho_fijo or _generar_texto_gancho(descargados)
        if texto_gancho:
            salida = _quemar_gancho(salida, texto_gancho, carpeta_tmp)
        else:
            _log_autopilot("Sin texto de gancho: el reel sigue sin él")
    except Exception as e:
        _log_autopilot(f"El paso de gancho falló ({e}), el reel sigue sin él")

    # Marca de agua después del gancho — no se solapan: el logo va arriba a la derecha
    # y el texto queda centrado al 25% del alto.
    aplicar_marca_de_agua(salida)

    aviso(f"🤖 <b>Paso 8/{total} — Claude generando SEO y thumbnail...</b>")
    preview = os.path.join(carpeta_tmp, "preview.mp4")
    _comprimir_preview(salida, preview)
    frames = extraer_frames_thumbnail(salida, cantidad=4)
    titulos = ", ".join(c.get("titulo", "")[:40] for c in descargados)
    seo = generar_seo(
        descripcion=contexto_seo or f"Compilación de videos virales de TikTok: {titulos}",
        tipo="YouTube Short viral",
        canal_handle="",
        idioma="Español",
        frames_b64=frames if frames else None,
        formato="reel",
        clips=descargados,
        candidatos_titulo=titulos_candidatos,
        jugador=jugador,
    )
    if titulos_candidatos:
        _elegir_titulo_autopilot(seo)
    if sin_encuesta:
        # Homenajes y cumpleaños de leyendas fallecidas: sin encuesta ni frase polémica.
        seo.pop("encuesta", None)

    guardar_en_historial([c["id"] for c in descargados])

    thumbnail = None
    try:
        from modules.thumbnail_gen import generar_thumbnail_automatico
        thumbnail = generar_thumbnail_automatico(salida, seo, carpeta_tmp)
    except Exception:
        pass

    _log_autopilot(
        f"Reel final: {_duracion_video(salida):.1f}s con {len(descargados)} clips"
    )

    return {
        "ok": True,
        "video": salida,
        "preview": preview if Path(preview).exists() else salida,
        "seo": seo,
        "clips_descargados": descargados,
        "thumbnail": thumbnail,
        "video_horizontal": None,
    }


def ejecutar_autopilot_pipeline(notify=None) -> dict:
    """
    Pipeline 100% headless del modo autopilot: sin ningún paso interactivo.
    Elige un hashtag combinado al azar (reintenta con otra combinación distinta si
    no hay clips suficientes, hasta 5 intentos), busca entre 3 y 5 TikToks de bajo
    perfil, recorta cada uno a su mejor tramo, los ordena por impacto, compila un
    Reel de 20-40s con gancho en pantalla y música aleatoria de Songs/, genera SEO
    y sube a YouTube.

    notify: callable(str) opcional para reportar progreso (mismo formato HTML que
    usa _compilar_reel_autopilot).

    Retorna {"ok": True, "url": str, "tema": str}. Ante cualquier fallo lanza
    RuntimeError con el motivo — nunca falla en silencio, el llamador decide cómo avisar.
    """
    def aviso(txt):
        if notify:
            notify(txt)

    MAX_INTENTOS = 5
    tema = None
    clips: list[dict] = []
    intentos_fallidos: set[str] = set()
    for intento in range(1, MAX_INTENTOS + 1):
        tema_candidato = _generar_tema_aleatorio(excluir=intentos_fallidos)
        aviso(
            f"🔍 <b>Autopilot — buscando clips de fútbol con buen engagement...</b>\n"
            f"Tema: #{tema_candidato} (intento {intento}/{MAX_INTENTOS})"
        )
        clips_candidatos = buscar_videos_autopilot(tema_candidato, min_clips=3, max_clips=5, avisar=False)
        if len(clips_candidatos) >= 3:
            tema = tema_candidato
            clips = clips_candidatos
            _marcar_tema_usado(tema)
            break
        intentos_fallidos.add(tema_candidato)

    if tema is None:
        raise RuntimeError(
            f"No encontré suficientes clips de fútbol con buen engagement tras "
            f"{MAX_INTENTOS} hashtags distintos ({', '.join('#' + t for t in intentos_fallidos)}). "
            f"Puede ser que la búsqueda por keyword de TikTok esté bloqueada, o que los "
            f"umbrales de vistas/likes sean muy estrictos."
        )

    from modules.music_manager import listar_tracks
    tracks = listar_tracks()
    if not tracks:
        raise RuntimeError("No hay ningún archivo de audio en Songs/ para usar como música de fondo.")
    musica = random.choice(tracks)["ruta"]

    resultado = _compilar_reel_autopilot(
        clips, musica, notify=notify,
        titulos_candidatos=True,
        jugador=_jugador_del_tema(tema),
    )
    if not resultado["ok"]:
        raise RuntimeError(resultado["error"])

    aviso("📤 <b>Autopilot — subiendo a YouTube...</b>")
    url = subir_a_youtube(resultado["video"], resultado["seo"], thumbnail=resultado.get("thumbnail"))
    if not url:
        raise RuntimeError("La subida a YouTube no devolvió una URL.")

    encuesta = resultado["seo"].get("encuesta")
    if encuesta and isinstance(encuesta, dict):
        try:
            video_id = url.split("v=")[-1].split("&")[0]
            postear_comentario_encuesta(video_id, encuesta)
        except Exception:
            pass

    return {"ok": True, "url": url, "tema": tema}


# ── Reel del día — efemérides (19:00 hora Israel) ─────────────────────────────
# Segundo reel diario, independiente del autopilot de las 13:00. Reutiliza el mismo
# pipeline de compilación (_compilar_reel_autopilot): copyright, recorte, orden,
# gancho, música y marca de agua. Lo único propio es de dónde sale el motivo —
# data/efemerides.json — y que el gancho y el título se arman desde ese dataset en
# vez de dejárselos inventar a Claude.

_FUENTES_REELS = Path(__file__).parent.parent / "data" / "fuentes_reels.json"

# Si la descripción que devuelve Claude contiene alguna de estas frases, la oración
# se descarta: nada de rumores ni de dar por confirmado lo que la fuente no dice.
_FRASES_PROHIBIDAS = (
    "se confirma", "se confirmó", "confirmado que", "rumor", "rumores",
    "habría", "trascendió", "al parecer", "según fuentes", "se especula",
    "versiones indican", "todo indica que",
)

# Pistas de Songs/ que suenan tranquilas, para homenajes y cumpleaños de leyendas
# ya fallecidas. Se matchea por nombre de archivo (ej. futbol1chill, futbol4piano).
_PISTAS_TRANQUILAS = ("chill", "piano", "calm", "slow", "sad", "emotional", "ambient")


def _musica_para(respetuosa: bool) -> str:
    """
    Ruta del track de fondo. Si el motivo pide tono neutro busca una pista tranquila
    en Songs/ y, si no hay ninguna, cae en una al azar — antes que quedarse sin música.
    """
    from modules.music_manager import listar_tracks
    tracks = listar_tracks()
    if not tracks:
        raise RuntimeError("No hay ningún archivo de audio en Songs/ para usar como música de fondo.")
    if respetuosa:
        tranquilas = [
            t for t in tracks
            if any(p in Path(t["ruta"]).stem.lower() for p in _PISTAS_TRANQUILAS)
        ]
        if tranquilas:
            elegida = random.choice(tranquilas)
            _log_autopilot(f"Motivo respetuoso: música tranquila → {Path(elegida['ruta']).name}")
            return elegida["ruta"]
        _log_autopilot("Motivo respetuoso pero no hay pista tranquila en Songs/: se usa una al azar")
    return random.choice(tracks)["ruta"]


def _sanear_descripcion(texto: str) -> str:
    """Saca las oraciones que afirmen cosas sin respaldo (rumores, 'se confirma', etc.)."""
    limpias, descartadas = [], 0
    for linea in texto.splitlines():
        if any(f in linea.lower() for f in _FRASES_PROHIBIDAS):
            descartadas += 1
            continue
        limpias.append(linea)
    if descartadas:
        _log_autopilot(f"Descripción saneada: {descartadas} línea(s) con frases no respaldadas")
    return "\n".join(limpias).strip()


def _registrar_fuentes(entrada: dict, clips: list[dict], url: str | None, titulo: str) -> None:
    """
    Guarda en data/fuentes_reels.json con qué se armó cada reel del día: la efeméride
    que lo motivó, su línea de contexto y los clips usados. Es el registro de fuentes
    que pide la regla de seguridad.
    """
    registro = {
        "fecha": datetime.now(_TZ_ARG).isoformat(),
        "origen": "efemeride",
        "efemeride_id": entrada["id"],
        "tipo": entrada["tipo"],
        "jugador": entrada["jugador"],
        "hashtag": entrada["hashtag"],
        "contexto": entrada["contexto"],
        "fuente": "data/efemerides.json",
        "titulo": titulo,
        "url": url,
        "clips": [
            {"id": c.get("id"), "titulo": c.get("titulo", "")[:120], "url": c.get("url")}
            for c in clips
        ],
    }
    try:
        historial = json.loads(_FUENTES_REELS.read_text(encoding="utf-8"))
    except Exception:
        historial = []
    historial.append(registro)
    _FUENTES_REELS.parent.mkdir(exist_ok=True)
    tmp = _FUENTES_REELS.with_suffix(".tmp")
    tmp.write_text(json.dumps(historial[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_FUENTES_REELS)


def _buscar_clips_efemeride(entrada: dict, aviso) -> tuple[list[dict], str | None]:
    """
    Busca clips del hashtag del jugador. Si el hashtag pelado no da suficientes,
    reintenta combinándolo con ángulos (goals, skills, edit...) igual que hace el
    autopilot con sus combinaciones. Devuelve (clips, hashtag_que_funcionó).
    """
    base = entrada["hashtag"]
    candidatos = [base] + [base + ang for ang in ("goals", "skills", "edit", "highlights")]
    for i, tag in enumerate(candidatos, 1):
        aviso(
            f"🔍 <b>Reel del día — buscando clips...</b>\n"
            f"Motivo: {entrada['jugador']} · Tema: #{tag} (intento {i}/{len(candidatos)})"
        )
        clips = buscar_videos_autopilot(tag, min_clips=3, max_clips=5, avisar=False)
        if len(clips) >= 3:
            return clips, tag
    return [], None


def ejecutar_reel_del_dia_pipeline(notify=None, dry_run: bool = False,
                                   hoy=None) -> dict:
    """
    Pipeline del segundo reel diario (19:00 hora Israel), 100% headless.

    Hoy el único motivo posible es una efeméride de data/efemerides.json. Si hoy no
    hay ninguna, NO se publica nada: devuelve {"ok": False, "sin_motivo": True} y el
    llamador avisa por Telegram. Eso es lo esperado, no un error.

    dry_run=True arma el reel completo pero no lo sube a YouTube, no marca la efeméride
    como usada y no escribe el registro de fuentes. Lo usa /noticia_test.

    hoy: date para simular otro día — solo para probar con /noticia_test DD/MM. El job
    de las 19:00 lo deja en None y usa la fecha real de Israel.

    Ante un fallo real lanza RuntimeError con el motivo — nunca falla en silencio.
    """
    from modules import efemerides

    def aviso(txt):
        if notify:
            notify(txt)

    hoy = hoy or efemerides.hoy_israel()
    entrada = efemerides.efemeride_de_hoy(hoy)
    if entrada is None:
        return {"ok": False, "sin_motivo": True, "fecha": hoy.isoformat()}

    respetuosa = efemerides.es_respetuosa(entrada)
    gancho = efemerides.texto_gancho(entrada, hoy)
    titulo = efemerides.titulo(entrada, hoy)
    _log_autopilot(
        f"Reel del día — {entrada['id']} ({entrada['tipo']}) · gancho={gancho!r} · "
        f"respetuoso={respetuosa} · dry_run={dry_run}"
    )

    clips, tag_usado = _buscar_clips_efemeride(entrada, aviso)
    if not clips:
        raise RuntimeError(
            f"No encontré clips para la efeméride de hoy ({entrada['jugador']}, "
            f"#{entrada['hashtag']}) tras probar el hashtag pelado y 4 variantes. "
            f"Puede que TikTok esté bloqueando la búsqueda o que el hashtag no tenga "
            f"videos que pasen los umbrales de vistas/likes."
        )

    musica = _musica_para(respetuosa)

    resultado = _compilar_reel_autopilot(
        clips, musica, notify=notify,
        gancho_fijo=gancho,
        contexto_seo=(
            f"YouTube Short de fútbol. Motivo del video: {entrada['contexto']} "
            f"Escribí la descripción basándote ÚNICAMENTE en ese dato y en los clips. "
            f"No afirmes nada que no esté ahí."
            + (" Tono sobrio y respetuoso, sin chistes ni frases polémicas." if respetuosa else "")
        ),
        sin_encuesta=respetuosa,
    )
    if not resultado["ok"]:
        raise RuntimeError(resultado["error"])

    # El título y el gancho salen del dataset, no de Claude.
    resultado["seo"]["titulo"] = titulo
    resultado["seo"]["descripcion"] = _sanear_descripcion(resultado["seo"].get("descripcion", ""))
    if entrada["contexto"] not in resultado["seo"]["descripcion"]:
        resultado["seo"]["descripcion"] = (
            entrada["contexto"] + "\n\n" + resultado["seo"]["descripcion"]
        ).strip()[:4990]

    salida = {
        "ok": True,
        "dry_run": dry_run,
        "efemeride": entrada,
        "gancho": gancho,
        "titulo": titulo,
        "hashtag_usado": tag_usado,
        "respetuosa": respetuosa,
        "musica": Path(musica).name,
        "video": resultado["video"],
        "preview": resultado["preview"],
        "seo": resultado["seo"],
        "clips": resultado["clips_descargados"],
        "url": None,
    }

    if dry_run:
        _log_autopilot("Dry-run: el reel quedó armado pero NO se sube a YouTube")
        return salida

    aviso("📤 <b>Reel del día — subiendo a YouTube...</b>")
    url = subir_a_youtube(
        resultado["video"], resultado["seo"], thumbnail=resultado.get("thumbnail")
    )
    if not url:
        raise RuntimeError("La subida a YouTube no devolvió una URL.")
    salida["url"] = url

    efemerides.marcar_usada(entrada, hoy)
    _registrar_fuentes(entrada, resultado["clips_descargados"], url, titulo)

    encuesta = resultado["seo"].get("encuesta")
    if encuesta and isinstance(encuesta, dict):
        try:
            video_id = url.split("v=")[-1].split("&")[0]
            postear_comentario_encuesta(video_id, encuesta)
        except Exception:
            pass

    return salida
