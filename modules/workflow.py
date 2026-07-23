"""
Orquestador del workflow de automatización.
Headless — sin Streamlit. Llama selector → compilador → SEO → YouTube.
"""
import base64
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import pytz

from modules.selector_ia import descargar_clips, guardar_en_historial
from modules.compilador import concatenar_clips, agregar_overlay_suscripcion
from modules.marca_agua import aplicar_marca_de_agua
from modules.seo_gen import generar_seo, extraer_frames_thumbnail, generar_capitulos

_TZ_ARG = pytz.timezone("America/Argentina/Buenos_Aires")
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

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

    tipo_label = "Short vertical (1080×1920)" if dual else f"{ancho}×{alto}"
    notify(f"✂️ <b>Paso {paso}/{total} — Compilando {len(descargados)} clips ({tipo_label})...</b>\n<i>Esto tarda 1-3 min según el largo</i>")
    paso += 1
    salida = os.path.join(carpeta_tmp, "compilacion.mp4")
    rutas = [c["ruta"] for c in descargados]
    # Para reels/ambos: recortar cada clip a 15s — YouTube Shorts distribuye mejor bajo 60s total
    max_seg_clip = 15 if formato in ("reel", "ambos") else None
    ok, err = concatenar_clips(rutas, salida, ancho, alto, max_seg_por_clip=max_seg_clip)
    if not ok:
        return {"ok": False, "error": f"Error en compilación: {err}"}

    # Compilar versión horizontal cuando se pidieron ambos formatos
    video_horizontal = None
    if dual:
        notify(f"📺 <b>Paso {paso}/{total} — Compilando versión horizontal (1920×1080) para YouTube Video...</b>")
        paso += 1
        salida_h = os.path.join(carpeta_tmp, "compilacion_horizontal.mp4")
        ok_h, _ = concatenar_clips(rutas, salida_h, 1920, 1080)
        if ok_h and Path(salida_h).exists():
            video_horizontal = salida_h

    # Teaser hook: Claude encuentra el momento más impactante y lo pone al inicio.
    # Solo para videos largos — en Shorts/Reels no se aplica porque agregar 5s extra
    # puede superar el límite de 60s y YouTube deja de clasificarlo como Short.
    if not dual and formato not in ("reel", "ambos"):
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
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None

    token_file = Path(__file__).parent.parent / "token_youtube.json"
    if not token_file.exists():
        raise RuntimeError("No se encontró token_youtube.json — autenticá desde Streamlit primero.")

    try:
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        if not creds.valid:
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

        return f"https://www.youtube.com/watch?v={video_id}"
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
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        return False

    token_file = Path(__file__).parent.parent / "token_youtube.json"
    if not token_file.exists():
        return False
    try:
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ]
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
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
