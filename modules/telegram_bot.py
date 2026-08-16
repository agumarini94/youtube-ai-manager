"""
Bot de Telegram para el workflow de aprobación.
Comandos: /trabajar, /urls, /estado, /cancelar

Flujo:
  /trabajar → ¿Formato? (Video / Reel)
            → Busca clips
            → Aprobás clips (thumbnails) [✅ Aprobar / 🔄 Buscar otros]
            → Compila + SEO
            → Preview del video + SEO [✅ Subir / 🔄 Recompilar]
            → Sube a YouTube
"""
import asyncio
import html
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone as _tz
from pathlib import Path

_logger   = logging.getLogger(__name__)
_TZ_ARG   = _tz(timedelta(hours=-3))

import requests
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from modules import flujo_estado as estado
from modules.music_manager import (
    listar_tracks, buscar_y_descargar,
    buscar_canciones, descargar_preview, descargar_desde_url,
    sugerir_musica_claude,
)
from modules.selector_ia import (
    CATEGORIAS,
    SUBCATEGORIAS,
    SUB_SUBCATEGORIAS,
    sub_tag,
    sub_pista,
    buscar_hashtags,
    filtrar_por_seguidores,
    seleccionar_con_claude,
    info_desde_urls,
    keywords_disponibles,
)
from modules.workflow import compilar_y_generar_seo, subir_a_youtube, postear_comentario_encuesta
from modules.texto_video import TIPOS_TEXTO, FONDOS, generar_texto_claude, generar_fondo_local, crear_video_texto
from modules.imagen_reel import generar_pregunta_claude, descargar_imagenes, crear_reel_imagenes
from modules.historia_reel import (
    SETTINGS, generar_historia_claude, crear_reel_historia, construir_descripcion,
)
from modules.eleccion_reel import (
    CATEGORIAS as CATEGORIAS_ELECCION,
    generar_eleccion_claude,
    crear_reel_eleccion,
    guardar_en_historial,
)
from modules.config import SECS_POR_PAGINA_TEXTO
from modules.thumbnail_gen import generar_thumbnail_automatico
from modules.voice_intro import generar_audio_intro, agregar_intro_voz
from modules.compilador import agregar_overlay_suscripcion
from modules.pronosticos_mundial import obtener_partidos_hoy, generar_pronosticos_claude, crear_video_pronosticos
from modules.narracion_reel import (
    listar_temas as listar_temas_narracion,
    generar_guion_claude as generar_guion_narracion,
    generar_audio_narracion,
    compilar_video_narracion,
    construir_seo as construir_seo_narracion,
    obtener_uso_elevenlabs,
    hay_creditos_para_guion,
)
from modules.pexels_video import descargar_para_narracion
from modules.wikipedia_images import descargar_personajes

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

_MAX_DUR = {"video": 60, "reel": 15, "ambos": 15}
# Reels: 3-4 clips de ≤15s = 45-60s total. YouTube Shorts distribuye mejor bajo 60s.
_N_CLIPS = {"video": 5, "reel": 4, "ambos": 4}

# Países disponibles para filtrar búsqueda — valor = sufijo de hashtag
PAISES = {
    "🌎 Global":     None,
    "🇦🇷 Argentina": "argentina",
    "🇲🇽 México":    "mexico",
    "🇧🇷 Brasil":    "brasil",
    "🇨🇴 Colombia":  "colombia",
    "🇨🇱 Chile":     "chile",
    "🇪🇸 España":    "espana",
    "🇺🇸 EEUU":      "usa",
    "🇷🇺 Rusia":     "russia",
}

FILTROS_TIEMPO = {
    "🕐 Últimas 24h":     24,
    "🕑 Últimas 48h":     48,
    "🕒 Últimas 72h":     72,
    "📆 Última semana":   24 * 7,
    "🗓️ Último mes":      24 * 30,
    "♾️ No importa la fecha": None,
}

# Orden ascendente de ventanas (None = sin límite) — se usa para calcular
# "la siguiente más amplia" cuando una búsqueda no encuentra resultados.
_ORDEN_FILTRO_TIEMPO = [24, 48, 72, 24 * 7, 24 * 30, None]
_SIN_SIGUIENTE = object()


def _siguiente_filtro_horas(actual: int | None):
    """Devuelve la próxima ventana más amplia después de `actual`.
    Retorna el sentinel _SIN_SIGUIENTE si ya no hay ninguna más amplia."""
    try:
        idx = _ORDEN_FILTRO_TIEMPO.index(actual)
    except ValueError:
        idx = -1  # actual no está en la lista → tratar como el más angosto
    if idx + 1 < len(_ORDEN_FILTRO_TIEMPO):
        return _ORDEN_FILTRO_TIEMPO[idx + 1]
    return _SIN_SIGUIENTE


def _label_filtro_horas(horas: int | None) -> str:
    """Etiqueta legible para un valor de horas, buscándolo en FILTROS_TIEMPO."""
    return next((k for k, v in FILTROS_TIEMPO.items() if v == horas), "sin límite de fecha")


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _responder_query(query, bot, texto: str, parse_mode: str = "HTML"):
    """
    Responde a un callback query de forma segura.
    Si el mensaje original es un video/foto (no tiene texto), quita los botones
    y manda un nuevo mensaje de texto en lugar de intentar editar el media.
    """
    try:
        await query.edit_message_text(texto, parse_mode=parse_mode)
    except Exception:
        # El mensaje es media (video, audio, foto) — no se puede editar como texto
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await bot.send_message(chat_id=CHAT_ID, text=texto, parse_mode=parse_mode)


def _fmt_dur(seg: int) -> str:
    m, s = divmod(seg, 60)
    return f"{m}:{s:02d}"


def _fmt_vistas(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n) if n > 0 else "N/D"


def _descargar_thumbnail(url: str) -> str | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name
    except Exception:
        return None


# ── Mensajes de aprobación ─────────────────────────────────────────────────────

async def _enviar_clips_para_aprobacion(bot: Bot, clips: list[dict]):
    for i, clip in enumerate(clips, 1):
        titulo = html.escape(clip.get("titulo", "Sin título")[:80])
        canal = html.escape(clip.get("canal", ""))
        url = clip.get("url", "")
        caption = (
            f"<b>Clip {i}</b>\n"
            f"📌 {titulo}\n"
            f"👤 @{canal}\n"
            f"⏱ {_fmt_dur(clip.get('duracion', 0))}  •  👁 {_fmt_vistas(clip.get('vistas', 0))}\n"
            f'🔗 <a href="{url}">Ver en TikTok</a>'
        )
        thumb = _descargar_thumbnail(clip.get("thumbnail", ""))
        try:
            if thumb:
                with open(thumb, "rb") as f:
                    await bot.send_photo(chat_id=CHAT_ID, photo=f, caption=caption, parse_mode="HTML")
                Path(thumb).unlink(missing_ok=True)
            else:
                await bot.send_message(chat_id=CHAT_ID, text=caption, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id=CHAT_ID, text=f"Clip {i}: {url}")

    # Botones: aprobar todo + reemplazar clip individual
    filas = [
        [
            InlineKeyboardButton("✅ Aprobar todos", callback_data="aprobar_clips"),
            InlineKeyboardButton("🔄 Buscar otros", callback_data="buscar_otros"),
        ]
    ]
    # Una fila de botones 🚫 por cada par de clips
    botones_clips = [
        InlineKeyboardButton(f"🚫 Clip {i}", callback_data=f"cambiar_{i}")
        for i in range(1, len(clips) + 1)
    ]
    for i in range(0, len(botones_clips), 2):
        filas.append(botones_clips[i:i+2])

    await bot.send_message(
        chat_id=CHAT_ID,
        text="¿Aprobás estos clips?\nTocá <b>🚫 Clip N</b> para reemplazar uno solo.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def _enviar_preview_y_seo(bot: Bot, preview: str, seo: dict, thumbnail: str | None = None, video_horizontal: str | None = None):
    """Envía el video preview + thumbnail + SEO con botones de aprobación."""
    titulo = seo.get("titulo", "Sin título")
    descripcion = seo.get("descripcion", "")
    tags = seo.get("tags", [])
    desc_preview = descripcion[:300] + ("..." if len(descripcion) > 300 else "")
    tags_txt = ", ".join(tags[:10])

    # 1. Intentar enviar el video preview
    preview_path = Path(preview)
    if preview_path.exists() and preview_path.stat().st_size < 48 * 1024 * 1024:
        try:
            with open(preview, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption="🎬 *Preview de la compilación* (480p comprimido)",
                    parse_mode="Markdown",
                    write_timeout=120,
                    read_timeout=120,
                )
        except Exception as e:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ No pude enviar el preview ({e}). Revisá el video en disco antes de aprobar."
            )
    else:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ El preview es muy grande para Telegram. Revisalo manualmente si querés."
        )

    # 2. Enviar thumbnail si existe
    if thumbnail and Path(thumbnail).exists():
        try:
            with open(thumbnail, "rb") as f:
                await bot.send_photo(
                    chat_id=CHAT_ID,
                    photo=f,
                    caption="🖼 <b>Thumbnail generado automáticamente</b>",
                    parse_mode="HTML",
                )
        except Exception:
            pass

    # 3. Enviar SEO + botones
    tiene_horizontal = video_horizontal and Path(video_horizontal).exists()
    nota_dual = "\n\n📺+🎬 <i>Se va a subir también la versión horizontal (YouTube Video).</i>" if tiene_horizontal else ""

    encuesta = seo.get("encuesta")
    encuesta_txt = ""
    if encuesta and isinstance(encuesta, dict):
        pregunta = encuesta.get("pregunta", "")
        opciones = encuesta.get("opciones", [])
        if pregunta and opciones:
            opciones_str = "\n".join(opciones)
            encuesta_txt = f"\n\n🗳️ <b>Encuesta (comentario fijado):</b>\n<i>{html.escape(pregunta)}</i>\n{html.escape(opciones_str)}"

    texto_seo = (
        f"📋 <b>SEO GENERADO</b>\n\n"
        f"📌 <b>Título:</b>\n{html.escape(titulo)}\n\n"
        f"📝 <b>Descripción:</b>\n{html.escape(desc_preview)}\n\n"
        f"🏷 <b>Tags:</b> {html.escape(tags_txt)}"
        f"{encuesta_txt}"
        f"{nota_dual}"
    )
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Subir a YouTube", callback_data="aprobar_seo"),
            InlineKeyboardButton("🔄 Recompilar",      callback_data="recompilar"),
        ],
        [InlineKeyboardButton("✏️ Regenerar SEO",      callback_data="regenerar_seo")],
        [InlineKeyboardButton("🎙️ Agregar voz intro",  callback_data="agregar_voz_intro")],
    ])
    await bot.send_message(
        chat_id=CHAT_ID, text=texto_seo, parse_mode="HTML", reply_markup=teclado
    )


# ── Funciones bloqueantes (se corren en executor) ──────────────────────────────

def _compilar_sync(clips: list[dict], formato: str, progress_cb=None,
                   musica: str | None = None, volumen: float = 0.15,
                   reemplazar_audio: bool = False) -> dict:
    return compilar_y_generar_seo(clips, formato=formato, progress_cb=progress_cb,
                                  musica=musica, volumen=volumen,
                                  reemplazar_audio=reemplazar_audio)


def _subir_sync(video: str, seo: dict, fecha_publicacion: str | None = None, thumbnail: str | None = None) -> str | None:
    return subir_a_youtube(video, seo, fecha_publicacion=fecha_publicacion, thumbnail=thumbnail)


# ── Pasos del flujo ────────────────────────────────────────────────────────────

async def _preguntar_formato(bot: Bot):
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✍️ Texto sobre video",   callback_data="formato_texto"),
            InlineKeyboardButton("🖼 Pregunta + Imágenes", callback_data="formato_imagen"),
        ],
        [
            InlineKeyboardButton("📖 Historia de hincha",  callback_data="formato_historia"),
            InlineKeyboardButton("🔢 ¿Cuál elegís?",       callback_data="formato_eleccion"),
        ],
        [
            InlineKeyboardButton("📺 YouTube Video",  callback_data="formato_video"),
            InlineKeyboardButton("🎬 Short / Reel",   callback_data="formato_reel"),
        ],
        [
            InlineKeyboardButton("📺+🎬 Ambos formatos", callback_data="formato_ambos"),
        ],
    ])
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "¿Qué tipo de contenido querés crear?\n\n"
            "<b>Generados con IA:</b>\n"
            "<i>✍️ Texto · 🖼 Pregunta + Imágenes · 📖 Historia · 🔢 ¿Cuál elegís?</i>\n\n"
            "<b>Compilaciones de TikTok:</b>\n"
            "<i>📺 Video largo · 🎬 Short · 📺+🎬 Ambos</i>"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_formato")


async def _preguntar_musica(bot: Bot):
    """Muestra los tracks disponibles en music/ para elegir música de fondo."""
    tracks = listar_tracks()

    # El botón de búsqueda siempre aparece, aunque la carpeta esté vacía
    filas = [[
        InlineKeyboardButton("🤖 Sugerida por IA", callback_data="musica_sugerida"),
        InlineKeyboardButton("🔍 Buscar canción",  callback_data="buscar_cancion"),
    ]]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"musica_{i+1}"))
        filas.append(fila)
    filas.append([InlineKeyboardButton("🔇 Sin música", callback_data="sin_musica")])

    estado.actualizar(estado="eligiendo_musica")
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🎵 <b>¿Agregar música de fondo?</b>\n\n"
            "<i>La música se mezcla a volumen bajo para no tapar el audio original. "
            "Podés elegir el volumen al confirmar la canción.</i>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


def _fmt_dur_cancion(seg: int) -> str:
    if not seg:
        return "?"
    return f"{seg // 60}:{seg % 60:02d}"


async def _mostrar_cancion(bot: Bot, idx: int, msg_id: int | None = None):
    """
    Descarga preview de 25s del resultado idx y lo envía con botones de navegación.
    Si msg_id se indica, edita ese mensaje en vez de crear uno nuevo.
    """
    datos = estado.leer()
    results = datos.get("canciones_resultados", [])

    if not results or idx >= len(results):
        await bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No hay más resultados. Buscá otra canción con /trabajar.",
        )
        estado.actualizar(estado="eligiendo_musica")
        await _preguntar_musica(bot)
        return

    cancion = results[idx]
    titulo = cancion["titulo"]
    dur_str = _fmt_dur_cancion(cancion.get("duracion", 0))
    estado.actualizar(cancion_idx=idx)

    # Editar mensaje de control a "descargando" si ya existe
    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=CHAT_ID, message_id=msg_id,
                text=f"⏳ <b>Descargando preview {idx + 1}/{len(results)}...</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Descargar preview (25s) en hilo secundario
    loop = asyncio.get_event_loop()
    preview_path = await loop.run_in_executor(None, descargar_preview, cancion["url"], 25)

    # Enviar audio preview
    if preview_path:
        try:
            with open(preview_path, "rb") as f:
                await bot.send_audio(
                    chat_id=CHAT_ID,
                    audio=f,
                    title=titulo[:64],
                    performer=f"Preview 25s · resultado {idx + 1}/{len(results)}",
                    duration=25,
                )
            from pathlib import Path as _Path
            _Path(preview_path).unlink(missing_ok=True)
        except Exception:
            pass

    hay_siguiente = idx + 1 < len(results)
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta", callback_data="cancion_usar"),
            InlineKeyboardButton(
                "▶️ Siguiente" if hay_siguiente else "🔄 Nueva búsqueda",
                callback_data="cancion_siguiente" if hay_siguiente else "buscar_cancion",
            ),
        ],
        [InlineKeyboardButton("🔇 Sin música", callback_data="sin_musica")],
    ])

    texto_ctrl = (
        f"🎵 <b>{idx + 1}/{len(results)}</b>: {html.escape(titulo[:90])}\n"
        f"⏱ {dur_str}\n\n"
        f"¿Querés usar esta canción de fondo?"
    )

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=CHAT_ID, message_id=msg_id,
                text=texto_ctrl, parse_mode="HTML", reply_markup=teclado,
            )
            return
        except Exception:
            pass

    msg = await bot.send_message(
        chat_id=CHAT_ID, text=texto_ctrl, parse_mode="HTML", reply_markup=teclado,
    )
    estado.actualizar(cancion_ctrl_msg_id=msg.message_id)


async def _preguntar_categoria(bot: Bot):
    """Muestra las categorías de búsqueda como botones inline (2 columnas)."""
    datos = estado.leer()
    vs_activo = bool(datos.get("vs_activo"))

    labels = list(CATEGORIAS.keys())
    filas = [
        [
            InlineKeyboardButton("✍️ Buscar por texto", callback_data="buscar_texto"),
            InlineKeyboardButton("🔗 Pegar links",      callback_data="pegar_links"),
        ],
        [
            InlineKeyboardButton("📷 Subir link de Instagram", callback_data="pegar_instagram"),
            InlineKeyboardButton("📱 Enviar de TikTok",        callback_data="enviar_tiktok"),
        ],
    ]
    if not vs_activo:
        filas.append([InlineKeyboardButton("🆚 Modo Versus", callback_data="vs_iniciar")])
    for i in range(0, len(labels), 2):
        fila = []
        for label in labels[i:i+2]:
            tag = CATEGORIAS[label]
            fila.append(InlineKeyboardButton(label, callback_data=f"cat_{tag}"))
        filas.append(fila)
    teclado = InlineKeyboardMarkup(filas)

    if vs_activo:
        total = datos.get("vs_total_lados", 2)
        lado_actual = datos.get("vs_lado_actual", 0) + 1
        texto = f"🆚 <b>Versus — Lado {lado_actual}/{total}</b>\n¿Qué querés buscar para este lado?"
    else:
        texto = "¿Qué tipo de videos querés buscar?"

    await bot.send_message(
        chat_id=CHAT_ID,
        text=texto,
        parse_mode="HTML" if vs_activo else None,
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_categoria")


async def cb_vs_iniciar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pregunta cuántos lados va a tener el Versus."""
    query = update.callback_query
    await query.answer()
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("2 lados", callback_data="vs_lados_2"),
        InlineKeyboardButton("4 lados", callback_data="vs_lados_4"),
    ]])
    await query.edit_message_text(
        "🆚 <b>Modo Versus</b>\n¿Cuántos lados querés comparar? (ej: Messi 🆚 Cristiano Ronaldo)",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_vs_lados(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Arranca el modo Versus con la cantidad de lados elegida (pattern: ^vs_lados_)."""
    query = update.callback_query
    await query.answer()
    total = int(query.data.split("_")[-1])
    estado.actualizar(vs_activo=True, vs_total_lados=total, vs_lado_actual=0, vs_lados=[])
    await query.edit_message_text(f"🆚 <b>Versus de {total} lados activado.</b>", parse_mode="HTML")
    await _preguntar_categoria(ctx.bot)


async def _preguntar_pais(bot: Bot):
    """Muestra botones de país antes de iniciar la búsqueda."""
    labels = list(PAISES.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [
            InlineKeyboardButton(label, callback_data=f"pais_{label}")
            for label in labels[i:i+2]
        ]
        filas.append(fila)
    await bot.send_message(
        chat_id=CHAT_ID,
        text="🌍 ¿De qué país querés los videos?",
        reply_markup=InlineKeyboardMarkup(filas),
    )
    estado.actualizar(estado="eligiendo_pais")


async def _preguntar_filtro_tiempo(bot: Bot):
    """Última pregunta antes de buscar: qué tan reciente debe ser el contenido."""
    labels = list(FILTROS_TIEMPO.keys())
    filas = [[InlineKeyboardButton(label, callback_data=f"filtrotiempo_{label}")] for label in labels]
    await bot.send_message(
        chat_id=CHAT_ID,
        text="⏳ ¿Qué tan reciente querés el contenido?",
        reply_markup=InlineKeyboardMarkup(filas),
    )
    estado.actualizar(estado="eligiendo_filtro_tiempo")


async def _iniciar_busqueda(bot: Bot):
    datos = estado.leer()
    formato = datos.get("formato", "video")
    tag = datos.get("tag_busqueda")   # puede ser None si no se eligió categoría
    pais = datos.get("pais_busqueda")  # None = global
    pista = datos.get("pista_busqueda")  # None si no hay subcategoría con pista
    max_horas = datos.get("max_horas_busqueda")  # None = sin límite de antigüedad
    n = _N_CLIPS[formato]
    max_dur = _MAX_DUR[formato]
    loop = asyncio.get_event_loop()
    estado.actualizar(estado="buscando")

    # Paso 1: buscar en hashtags
    etiqueta_cat = next((k for k, v in CATEGORIAS.items() if v == tag), tag or "tendencias generales")
    etiqueta_pais = next((k for k, v in PAISES.items() if v == pais), "") if pais else ""
    etiqueta_display = f"{etiqueta_cat}" + (f" · {etiqueta_pais}" if etiqueta_pais else "")
    filtro_horas_txt = f" · últimas {max_horas}h" if max_horas else ""
    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🔍 <b>Paso 1/3</b> — Buscando en TikTok: <i>{html.escape(etiqueta_display + filtro_horas_txt)}</i>...",
        parse_mode="HTML",
    )
    candidatos = await loop.run_in_executor(
        None, lambda: buscar_hashtags(max_dur, tag, pais, max_horas=max_horas)
    )

    if not keywords_disponibles():
        await bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ Búsqueda por keywords no disponible (bloqueo de Cloudflare en tikwm.com). Resultados solo por hashtag.",
        )

    _teclado_rebuscar = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Cambiar búsqueda", callback_data="buscar_otros"),
    ]])

    def _teclado_sin_resultados() -> InlineKeyboardMarkup:
        """Igual que _teclado_rebuscar, pero suma un botón para ampliar la ventana
        de fecha si todavía queda una más amplia disponible."""
        filas = [[InlineKeyboardButton("🔄 Cambiar búsqueda", callback_data="buscar_otros")]]
        siguiente = _siguiente_filtro_horas(max_horas)
        if siguiente is not _SIN_SIGUIENTE:
            sufijo = str(siguiente) if siguiente is not None else "none"
            texto_label = _label_filtro_horas(siguiente).split(" ", 1)[-1]
            filas.append([InlineKeyboardButton(
                f"🔎 Ampliar a {texto_label}",
                callback_data=f"ampliarfiltro_{sufijo}",
            )])
        return InlineKeyboardMarkup(filas)

    if not candidatos:
        estado.actualizar(estado="eligiendo_categoria")
        await msg.edit_text(
            "❌ <b>No encontré videos</b> con esos filtros.\n\n"
            "Podés cambiar la búsqueda o pegar links:\n"
            "/urls https://tiktok.com/...",
            parse_mode="HTML",
            reply_markup=_teclado_sin_resultados(),
        )
        return

    # Paso 2: filtrar por seguidores
    unique_ids = len({c["canal"] for c in candidatos if c.get("canal")})
    await msg.edit_text(
        f"👥 <b>Paso 2/3</b> — Verificando {unique_ids} creadores "
        f"(filtro: &lt;2M seguidores)...",
        parse_mode="HTML",
    )
    candidatos = await loop.run_in_executor(None, filtrar_por_seguidores, candidatos, 2_000_000)

    if not candidatos:
        estado.actualizar(estado="eligiendo_categoria")
        await msg.edit_text(
            "❌ <b>Sin resultados</b> — todos los videos son de cuentas con +2M seguidores.\n"
            "Probá con otra categoría o país.",
            parse_mode="HTML",
            reply_markup=_teclado_sin_resultados(),
        )
        return

    # Paso 3: Claude elige los mejores
    await msg.edit_text(
        f"🤖 <b>Paso 3/3</b> — Claude eligiendo los mejores {n} clips de {len(candidatos)} candidatos...",
        parse_mode="HTML",
    )
    clips = await loop.run_in_executor(None, seleccionar_con_claude, candidatos, n, pista)

    if not clips:
        estado.actualizar(estado="eligiendo_categoria")
        await msg.edit_text(
            "❌ <b>No pude seleccionar clips.</b> Probá con otra categoría.",
            parse_mode="HTML",
            reply_markup=_teclado_rebuscar,
        )
        return

    await msg.edit_text(f"✅ ¡Encontré {len(clips)} clips! Revisalos:")
    # Guardar los candidatos no seleccionados como pool para reemplazos individuales
    ids_seleccionados = {c["id"] for c in clips}
    pool = [c for c in candidatos if c["id"] not in ids_seleccionados]
    estado.actualizar(estado="aprobacion_clips", clips=clips, candidatos_pool=pool)
    await _enviar_clips_para_aprobacion(bot, clips)


async def _iniciar_compilacion(bot: Bot):
    datos = estado.leer()
    clips = datos.get("clips", [])
    formato = datos.get("formato", "video")

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⏳ <b>Iniciando compilación...</b>",
        parse_mode="HTML",
    )
    estado.actualizar(estado="compilando")

    loop = asyncio.get_event_loop()

    def progress_cb(texto):
        asyncio.run_coroutine_threadsafe(
            msg.edit_text(texto, parse_mode="HTML"),
            loop,
        )

    musica = datos.get("musica_ruta")
    volumen = datos.get("musica_volumen", 0.15)
    reemplazar_audio = datos.get("musica_reemplazar", False)
    resultado = await loop.run_in_executor(
        None, _compilar_sync, clips, formato, progress_cb, musica, volumen, reemplazar_audio
    )

    if not resultado["ok"]:
        estado.reset()
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ {resultado['error']}")
        return

    estado.actualizar(
        estado="aprobacion_preview",
        video_compilado=resultado["video"],
        video_preview=resultado["preview"],
        seo=resultado["seo"],
        clips_descargados=resultado.get("clips_descargados", []),
        thumbnail=resultado.get("thumbnail"),
        video_horizontal=resultado.get("video_horizontal"),
    )
    await _enviar_preview_y_seo(
        bot, resultado["preview"], resultado["seo"],
        resultado.get("thumbnail"), resultado.get("video_horizontal"),
    )


def _parsear_hora_custom(texto_usuario: str) -> str | None:
    """
    Parsea una fecha/hora en lenguaje natural usando Claude.
    Retorna ISO UTC string o None si no puede parsear.
    """
    import anthropic as _anthropic
    import json as _json
    import pytz as _pytz
    from datetime import datetime as _dt

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    tz_arg = _pytz.timezone("America/Argentina/Buenos_Aires")
    ahora = _dt.now(tz_arg)
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_hoy = dias_es[ahora.weekday()]

    client = _anthropic.Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": (
                f"Hoy es {dia_hoy} {ahora.strftime('%Y-%m-%d')} a las {ahora.strftime('%H:%M')} "
                f"(hora Argentina, GMT-3).\n"
                f'El usuario quiere programar un video para: "{texto_usuario}"\n\n'
                f"Interpretá la fecha y hora y respondé SOLO con JSON válido:\n"
                f'{{"fecha_local":"YYYY-MM-DD","hora_local":"HH:MM"}}\n'
                f"Si no podés interpretar, respondé: null"
            )}],
        )
        raw = resp.content[0].text.strip()
        if raw.lower() == "null":
            return None
        data = _json.loads(raw)
        dt_local = tz_arg.localize(
            _dt.strptime(f"{data['fecha_local']} {data['hora_local']}", "%Y-%m-%d %H:%M")
        )
        # Verificar que la fecha sea en el futuro
        if dt_local <= ahora:
            return None
        return dt_local.astimezone(_pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _calcular_opciones_horario() -> list[dict]:
    """
    Calcula las opciones de horario disponibles para publicar.
    Retorna lista de dicts con 'label' e 'iso_utc', ordenadas cronológicamente.
    """
    import pytz as _pytz
    from datetime import datetime as _dt, timedelta as _td

    tz_arg = _pytz.timezone("America/Argentina/Buenos_Aires")
    ahora = _dt.now(tz_arg)
    buenos_dias = {1, 2, 3, 4}   # mar–vie
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    opciones: list[dict] = []

    # Franjas de HOY — siempre, cualquier día de la semana
    for h in [18, 19, 20]:
        if h > ahora.hour:
            dt = ahora.replace(hour=h, minute=0, second=0, microsecond=0)
            dt_utc = dt.astimezone(_pytz.utc)
            opciones.append({
                "label": f"Hoy {h:02d}:00",
                "iso_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

    # Próximos 2 días buenos con dos franjas cada uno
    encontrados = 0
    for delta in range(1, 8):
        if encontrados >= 2:
            break
        candidato = ahora + _td(days=delta)
        if candidato.weekday() in buenos_dias:
            nombre = dias_es[candidato.weekday()].capitalize()
            fecha = candidato.strftime("%d/%m")
            for h in [18, 20]:
                dt = candidato.replace(hour=h, minute=0, second=0, microsecond=0)
                dt_utc = dt.astimezone(_pytz.utc)
                opciones.append({
                    "label": f"{nombre} {fecha} {h:02d}:00",
                    "iso_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            encontrados += 1

    return opciones


async def _recomendar_horario(bot: Bot):
    """Muestra botones de horario predefinidos + opción de hora custom + volver."""
    opciones = _calcular_opciones_horario()
    estado.actualizar(estado="aprobacion_horario", opciones_horario=opciones)

    # Fila de "Subir ahora" sola arriba
    filas = [[InlineKeyboardButton("⚡ Subir ahora", callback_data="subir_ahora")]]

    # Opciones de horario en pares de 2 por fila
    fila_actual: list[InlineKeyboardButton] = []
    for i, op in enumerate(opciones):
        fila_actual.append(
            InlineKeyboardButton(f"📅 {op['label']}", callback_data=f"hpreset_{i}")
        )
        if len(fila_actual) == 2:
            filas.append(fila_actual)
            fila_actual = []
    if fila_actual:
        filas.append(fila_actual)

    # Última fila: hora custom + volver
    filas.append([
        InlineKeyboardButton("✏️ Otra hora", callback_data="elegir_hora"),
        InlineKeyboardButton("⬅️ Volver", callback_data="volver_seo"),
    ])

    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "📅 <b>¿Cuándo querés publicar?</b>\n\n"
            "<i>Los horarios sugeridos son las franjas pico para audiencia hispanohablante "
            "(martes a viernes, 18–20 h Argentina).</i>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def _iniciar_subida(bot: Bot, fecha_publicacion: str | None = None):
    datos = estado.leer()
    video = datos.get("video_compilado")
    seo = datos.get("seo") or {}

    if not video or not Path(video).exists():
        estado.reset()
        await bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No encontré el video compilado. Iniciá de nuevo con /trabajar."
        )
        return

    thumbnail = datos.get("thumbnail")
    video_horizontal = datos.get("video_horizontal")
    tiene_horizontal = video_horizontal and Path(video_horizontal).exists()

    # Auto-generar thumbnail para videos largos si no existe
    loop = asyncio.get_event_loop()
    if not thumbnail and video and datos.get("formato") in ("video", "ambos"):
        seo_para_thumb = datos.get("seo") or {}
        try:
            thumbnail = await loop.run_in_executor(
                None, generar_thumbnail_automatico, video, seo_para_thumb, None
            )
        except Exception:
            thumbnail = None  # No crítico, seguir sin thumbnail

    if fecha_publicacion:
        await bot.send_message(chat_id=CHAT_ID, text="📤 Subiendo a YouTube como privado y programando publicación...")
    else:
        await bot.send_message(chat_id=CHAT_ID, text="📤 Subiendo a YouTube... (puede tardar ~2 min)")
    estado.actualizar(estado="subiendo")

    try:
        url = await loop.run_in_executor(None, _subir_sync, video, seo, fecha_publicacion, thumbnail)
    except Exception as e:
        estado.reset()
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error inesperado al subir a YouTube:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        return

    # Si hay versión horizontal, subirla también
    url_horizontal = None
    if tiene_horizontal and url:
        await bot.send_message(chat_id=CHAT_ID, text="📺 Subiendo versión horizontal (YouTube Video)...")
        try:
            url_horizontal = await loop.run_in_executor(None, _subir_sync, video_horizontal, seo, fecha_publicacion, thumbnail)
        except Exception:
            url_horizontal = None

    # Postear encuesta como comentario en el video subido
    encuesta = seo.get("encuesta")
    if url and encuesta and isinstance(encuesta, dict):
        try:
            video_id = url.split("v=")[-1].split("&")[0]
            ok_enc = await loop.run_in_executor(
                None, postear_comentario_encuesta, video_id, encuesta
            )
            if ok_enc:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text="🗳️ Encuesta posteada como comentario. Fijala desde YouTube Studio para máximo engagement.",
                    parse_mode="HTML",
                )
        except Exception:
            pass

    estado.reset()
    rec = datos.get("recomendacion_horario") or {}
    label = html.escape(rec.get("label", fecha_publicacion or ""))
    if fecha_publicacion:
        texto = f"✅ <b>Short programado para {label}:</b>\n{url}"
        if url_horizontal:
            texto += f"\n\n📺 <b>Video horizontal programado para {label}:</b>\n{url_horizontal}"
        elif tiene_horizontal:
            texto += "\n\n⚠️ El video horizontal no se pudo subir."
    else:
        texto = f"✅ <b>Short publicado:</b>\n{url}"
        if url_horizontal:
            texto += f"\n\n📺 <b>Video horizontal publicado:</b>\n{url_horizontal}"
        elif tiene_horizontal:
            texto += "\n\n⚠️ El video horizontal no se pudo subir."
    await bot.send_message(chat_id=CHAT_ID, text=texto, parse_mode="HTML")


# ── Handlers de comandos ────────────────────────────────────────────────────────

async def cmd_trabajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"Ya hay un proceso activo: *{datos['estado']}*\nUsá /cancelar para reiniciar.",
            parse_mode="Markdown",
        )
        return
    await _preguntar_formato(ctx.bot)


async def cmd_urls(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    if not ctx.args:
        await update.message.reply_text(
            "Uso: /urls <url1> <url2> ...\nEjemplo:\n/urls https://tiktok.com/..."
        )
        return
    await update.message.reply_text("🔍 Obteniendo info de los videos...")
    loop = asyncio.get_event_loop()
    clips = await loop.run_in_executor(None, info_desde_urls, list(ctx.args))
    if not clips:
        await update.message.reply_text("❌ No pude obtener info de esas URLs.")
        return
    estado.actualizar(estado="aprobacion_clips", clips=clips, chat_id=CHAT_ID)
    await _enviar_clips_para_aprobacion(ctx.bot, clips)


async def cmd_estado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    texto = f"📊 *Estado:* `{datos['estado']}`\n🎬 *Formato:* `{datos.get('formato','video')}`"
    if datos.get("timestamp"):
        texto += f"\n🕒 {datos['timestamp'][:19]}"
    if datos.get("clips"):
        texto += f"\n📹 Clips: {len(datos['clips'])}"
    await update.message.reply_text(texto, parse_mode="Markdown")


async def cmd_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    estado.reset()
    await update.message.reply_text("🔄 Proceso cancelado. Estado reiniciado.")


async def cmd_ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    import datetime
    hora = datetime.datetime.now().strftime("%H:%M:%S")
    datos = estado.leer()
    est = datos.get("estado", "idle")
    await update.message.reply_text(
        f"🟢 <b>Bot online</b> — {hora}\n"
        f"📊 Estado actual: <code>{est}</code>",
        parse_mode="HTML",
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Muestra las últimas 10 videos del canal con views, likes y comentarios."""
    if update.effective_chat.id != CHAT_ID:
        return

    await update.message.reply_text("📊 Obteniendo estadísticas del canal...", parse_mode="HTML")

    try:
        from pathlib import Path as _Path
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_file = _Path(__file__).parent.parent / "token_youtube.json"
        if not token_file.exists():
            await update.message.reply_text("❌ No hay token de YouTube. Autenticá desde Streamlit primero.")
            return

        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        if not creds.valid:
            await update.message.reply_text("❌ Credenciales de YouTube inválidas. Reautenticá desde Streamlit.")
            return

        yt = build("youtube", "v3", credentials=creds)

        # Obtener el canal propio
        canal = yt.channels().list(part="id,snippet", mine=True).execute()
        items = canal.get("items", [])
        if not items:
            await update.message.reply_text("❌ No se encontró ningún canal asociado a la cuenta.")
            return
        channel_id = items[0]["id"]
        channel_name = items[0]["snippet"]["title"]

        # Buscar los últimos 10 videos del canal
        search_resp = yt.search().list(
            part="id",
            channelId=channel_id,
            order="date",
            maxResults=10,
            type="video",
        ).execute()
        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]

        if not video_ids:
            await update.message.reply_text(f"📭 No se encontraron videos en el canal <b>{channel_name}</b>.", parse_mode="HTML")
            return

        # Obtener estadísticas de esos videos
        stats_resp = yt.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids),
        ).execute()

        lineas = [f"📊 <b>Últimos videos — {channel_name}</b>\n"]
        for v in stats_resp.get("items", []):
            titulo = v["snippet"]["title"][:50]
            stats = v.get("statistics", {})
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            comentarios = int(stats.get("commentCount", 0))
            vid = v["id"]
            lineas.append(
                f"▶️ <a href='https://youtu.be/{vid}'>{titulo}</a>\n"
                f"   👁 {views:,}  ·  👍 {likes:,}  ·  💬 {comentarios:,}"
            )

        await update.message.reply_text(
            "\n\n".join(lineas),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Error al obtener stats: <code>{str(e)[:200]}</code>", parse_mode="HTML")


# ── Callbacks inline ────────────────────────────────────────────────────────────

async def cb_formato_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📺 <b>Formato: YouTube Video</b> (clips hasta 60s, 1920×1080)", parse_mode="HTML")
    estado.actualizar(formato="video", chat_id=CHAT_ID)
    await _preguntar_categoria(ctx.bot)


async def cb_formato_reel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎬 <b>Formato: Short / Reel</b> (clips hasta 15s, 1080×1920)", parse_mode="HTML")
    estado.actualizar(formato="reel", chat_id=CHAT_ID)
    await _preguntar_categoria(ctx.bot)


async def cb_formato_ambos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📺+🎬 <b>Ambos formatos</b> — Short vertical (1080×1920) + Video horizontal (1920×1080)\n"
        "<i>Se van a compilar y subir los dos con los mismos clips.</i>",
        parse_mode="HTML",
    )
    estado.actualizar(formato="ambos", chat_id=CHAT_ID)
    await _preguntar_categoria(ctx.bot)


async def cb_formato_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✍️ <b>Shorts de texto</b>", parse_mode="HTML")
    labels = list(TIPOS_TEXTO.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [InlineKeyboardButton(label, callback_data=f"txt_tipo_{TIPOS_TEXTO[label]}")
                for label in labels[i:i+2]]
        filas.append(fila)
    estado.actualizar(estado="txt_eligiendo_tipo")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "✍️ <b>Shorts de texto</b>\n\n"
            "¿Qué tipo de contenido querés crear?\n"
            "<i>Claude genera el texto, vos elegís el fondo y la música.</i>"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_formato_imagen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🖼 <b>Reel de pregunta viral</b>", parse_mode="HTML")
    await _mostrar_menu_temas_imagen(ctx.bot)


async def cb_formato_historia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📖 <b>Historia de Hincha</b>", parse_mode="HTML")
    labels = list(SETTINGS.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [
            InlineKeyboardButton(label, callback_data=f"his_setting_{i + j}")
            for j, label in enumerate(labels[i:i+2])
            if i + j < len(labels)
        ]
        filas.append(fila)
    estado.actualizar(estado="his_eligiendo_setting")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "📖 <b>Historia de Hincha</b>\n\n"
            "Claude escribe una historia en lunfardo para la descripción del video.\n\n"
            "¿Cuál es tu relación con el fútbol?"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_musica_elegida(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible. Usá /cancelar y empezá de nuevo.")
        return
    track = tracks[idx]
    estado.actualizar(musica_ruta=track["ruta"], musica_nombre=track["nombre"], musica_seleccionada=True)
    await query.edit_message_text(
        f"🎵 <b>{html.escape(track['nombre'])}</b> elegida como música de fondo.",
        parse_mode="HTML",
    )
    await _iniciar_compilacion(ctx.bot)


async def cb_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 <b>Sin música de fondo.</b>", parse_mode="HTML")
    estado.actualizar(musica_ruta=None, musica_nombre=None, musica_seleccionada=True)
    await _iniciar_compilacion(ctx.bot)


async def cb_musica_sugerida(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Claude elige la canción más adecuada para el video actual."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🤖 <b>Claude está eligiendo la canción...</b>", parse_mode="HTML")

    datos = estado.leer()
    # Construir contexto según el tipo de contenido
    partes = []
    tag = datos.get("tag_busqueda")
    if tag:
        partes.append(f"Compilación de TikTok — categoría: {tag}")
    if datos.get("txt_tipo"):
        partes.append(f"Video de texto tipo '{datos['txt_tipo']}' sobre '{datos.get('txt_tema', '')}'")
    if datos.get("img_tema"):
        partes.append(f"Reel de pregunta viral — tema: {datos['img_tema']}")
    if datos.get("his_setting_label"):
        partes.append(f"Historia de hincha — contexto: {datos['his_setting_label']}")
    if datos.get("elc_categoria"):
        partes.append(f"Reel '¿Cuál elegís?' — categoría: {datos['elc_categoria']}")
    clips = datos.get("clips", [])
    if clips:
        titulos = ", ".join(c.get("titulo", "")[:40] for c in clips[:3])
        partes.append(f"Clips seleccionados: {titulos}")

    contexto = " | ".join(partes) if partes else "Video de fútbol para YouTube Shorts"

    loop = asyncio.get_event_loop()
    sugerencia = await loop.run_in_executor(None, sugerir_musica_claude, contexto)

    if not sugerencia or not sugerencia.get("busqueda"):
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No pude obtener sugerencia. Usá <b>Buscar canción</b> manualmente.",
            parse_mode="HTML",
        )
        await _preguntar_musica(ctx.bot)
        return

    busqueda = sugerencia["busqueda"]
    razon    = sugerencia.get("razon", "")

    msg = await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🎵 Claude sugiere: <b>{html.escape(busqueda)}</b>\n<i>{html.escape(razon)}</i>\n\nBuscando en YouTube...",
        parse_mode="HTML",
    )

    results = await loop.run_in_executor(None, buscar_canciones, busqueda, 5)
    if not results:
        await msg.edit_text(
            f"❌ No encontré <b>{html.escape(busqueda)}</b> en YouTube.\n"
            "Usá <b>Buscar canción</b> para buscar manualmente.",
            parse_mode="HTML",
        )
        return

    estado.actualizar(canciones_resultados=results, cancion_idx=0, estado="eligiendo_cancion")
    await msg.edit_text(
        f"✅ <b>{html.escape(busqueda)}</b> — {len(results)} resultados. Escuchá el preview:",
        parse_mode="HTML",
    )
    await _mostrar_cancion(ctx.bot, idx=0)


async def cb_buscar_cancion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 <b>Escribí el nombre de la canción</b>\n\n"
        "Podés poner artista + nombre, por ejemplo:\n"
        "<code>Flowers Miley Cyrus</code>\n"
        "<code>phonk drift 2025</code>\n"
        "<code>música viral tiktok 2025</code>",
        parse_mode="HTML",
    )
    estado.actualizar(estado="esperando_nombre_cancion")


async def cb_cancion_siguiente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Avanza al siguiente resultado de búsqueda de canción."""
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0) + 1
    await _mostrar_cancion(ctx.bot, idx=idx, msg_id=query.message.message_id)


async def cb_cancion_usar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Confirma la canción y pregunta si sacar el audio original de los TikToks."""
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    titulo = results[idx]["titulo"] if results and idx < len(results) else "la canción"

    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔇 Sí, sacar audio (música 100%)", callback_data="audio_sacar")],
        [InlineKeyboardButton("🎙️ No, mantener audio original",   callback_data="audio_mantener")],
    ])
    await query.edit_message_text(
        f"🎵 <b>{html.escape(titulo[:80])}</b>\n\n"
        f"🎧 ¿Querés <b>sacar el sonido original</b> de los TikToks y dejar solo la música?",
        parse_mode="HTML",
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_audio_original")


async def cb_audio_mantener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mantiene el audio original: muestra selector de volumen para mezcla."""
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    titulo = results[idx]["titulo"] if results and idx < len(results) else "la canción"

    estado.actualizar(musica_reemplazar=False)

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔉 Suave (8%)",  callback_data="vol_008"),
            InlineKeyboardButton("🎵 Normal (15%)", callback_data="vol_015"),
            InlineKeyboardButton("🔊 Alta (25%)",   callback_data="vol_025"),
        ],
        [InlineKeyboardButton("🔇 Sin música", callback_data="sin_musica")],
    ])
    await query.edit_message_text(
        f"🎵 <b>{html.escape(titulo[:80])}</b>\n\n"
        f"🔊 ¿A qué volumen querés la música de fondo?",
        parse_mode="HTML",
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_volumen_musica")


async def cb_audio_sacar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reemplaza el audio original por la música al 100% e inicia compilación."""
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await query.edit_message_text("❌ Error. Usá /cancelar y empezá de nuevo.")
        return

    cancion = results[idx]
    await query.edit_message_text(
        f"⏳ Descargando <b>{html.escape(cancion['titulo'][:70])}</b>...",
        parse_mode="HTML",
    )

    loop = asyncio.get_event_loop()
    track = await loop.run_in_executor(None, descargar_desde_url, cancion["url"])
    if not track:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ Error descargando la canción. Intentá con otra.",
        )
        estado.actualizar(estado="eligiendo_musica")
        await _preguntar_musica(ctx.bot)
        return

    estado.actualizar(
        musica_ruta=track["ruta"],
        musica_nombre=track["nombre"],
        musica_volumen=1.0,
        musica_reemplazar=True,
        musica_seleccionada=True,
    )
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"✅ <b>{html.escape(track['nombre'][:70])}</b> lista.\n"
            f"<i>Audio original silenciado · música al 100%.</i>"
        ),
        parse_mode="HTML",
    )
    await _iniciar_compilacion(ctx.bot)


async def cb_volumen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Descarga la canción completa con el volumen elegido e inicia compilación."""
    query = update.callback_query
    await query.answer()

    vol_int = int(query.data.split("_")[1])   # "vol_015" → 15
    volumen = vol_int / 100                    # 0.15
    vol_label = {8: "suave (8%)", 15: "normal (15%)", 25: "alta (25%)"}.get(vol_int, f"{vol_int}%")

    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await query.edit_message_text("❌ Error. Usá /cancelar y empezá de nuevo.")
        return

    cancion = results[idx]
    await query.edit_message_text(
        f"⏳ Descargando <b>{html.escape(cancion['titulo'][:70])}</b>...",
        parse_mode="HTML",
    )

    loop = asyncio.get_event_loop()
    track = await loop.run_in_executor(None, descargar_desde_url, cancion["url"])
    if not track:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ Error descargando la canción. Intentá con otra.",
        )
        estado.actualizar(estado="eligiendo_musica")
        await _preguntar_musica(ctx.bot)
        return

    estado.actualizar(
        musica_ruta=track["ruta"],
        musica_nombre=track["nombre"],
        musica_volumen=volumen,
        musica_seleccionada=True,
    )
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"✅ <b>{html.escape(track['nombre'][:70])}</b> lista.\n"
            f"<i>Volumen: {vol_label} · guardada en music/ para la próxima vez.</i>"
        ),
        parse_mode="HTML",
    )
    await _iniciar_compilacion(ctx.bot)


async def cb_buscar_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✍️ <b>Escribí el tema que querés buscar</b>\n\n"
        "Por ejemplo: <code>caidas</code>, <code>fails deportivos</code>, <code>gatos graciosos</code>",
        parse_mode="HTML",
    )
    estado.actualizar(estado="esperando_keyword")


async def cb_pegar_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔗 <b>Pegá los links de TikTok que querés usar</b>\n\n"
        "Podés pegar uno o más, separados por espacio o en líneas distintas.\n"
        "Ejemplo: <code>https://www.tiktok.com/@user/video/123...</code>",
        parse_mode="HTML",
    )
    estado.actualizar(estado="esperando_urls")


async def cb_enviar_tiktok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Activa el modo 'enviar videos de TikTok de a uno' (pattern: ^enviar_tiktok$)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📱 <b>Mandame los videos de TikTok</b>\n\n"
        "Compartilos desde la app (de a uno, llegan como link). "
        "Cuando termines, tocá el botón de compilar.",
        parse_mode="HTML",
    )
    estado.actualizar(estado="esperando_tiktok", tiktok_pendientes=[], tiktok_msg_id=None)


async def cb_tiktok_agregar_mas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ack visual — los videos ya se suman solos al llegar (pattern: ^tiktok_agregar_mas$)."""
    query = update.callback_query
    await query.answer("Seguí mandando videos 📱")


async def cb_tiktok_listo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cierra la carga de TikTok y pasa a aprobación de clips (pattern: ^tiktok_listo$).
    Siempre usa la lista completa actual de `tiktok_pendientes`, sin importar desde
    qué mensaje (viejo o nuevo) se haya tocado el botón."""
    query = update.callback_query
    datos = estado.leer()
    clips = datos.get("tiktok_pendientes", [])
    if not clips:
        await query.answer("Todavía no mandaste ningún video.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"✅ {len(clips)} video(s) listos. Revisalos:")
    estado.actualizar(estado="aprobacion_clips", clips=clips, tiktok_pendientes=[], tiktok_msg_id=None)
    await _enviar_clips_para_aprobacion(ctx.bot, clips)


async def _preguntar_subcategoria(bot: Bot, label: str):
    """Muestra los botones de subcategoría para una categoría dada."""
    subs = SUBCATEGORIAS[label]
    filas = []
    items = list(subs.items())
    for i in range(0, len(items), 2):
        fila = []
        for slabel, sval in items[i:i+2]:
            fila.append(InlineKeyboardButton(slabel, callback_data=f"sub_{sub_tag(sval)}"))
        filas.append(fila)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🗂 <b>{html.escape(label)}</b>\n¿Qué subcategoría querés buscar?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )
    estado.actualizar(estado="eligiendo_subcategoria")


async def cb_categoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler genérico para cualquier botón de categoría (pattern: ^cat_)."""
    query = update.callback_query
    await query.answer()
    tag = query.data[4:]   # quita el prefijo "cat_"
    label = next((k for k, v in CATEGORIAS.items() if v == tag), tag)
    await query.edit_message_text(f"🎯 Categoría elegida: <b>{html.escape(label)}</b>", parse_mode="HTML")
    estado.actualizar(tag_busqueda=tag)
    if label in SUBCATEGORIAS:
        await _preguntar_subcategoria(ctx.bot, label)
    else:
        await _preguntar_pais(ctx.bot)


async def cb_subcategoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler para botones de subcategoría (pattern: ^sub_)."""
    query = update.callback_query
    await query.answer()
    tag_buscado = query.data[4:]   # quita el prefijo "sub_"

    # Reverse-lookup: buscar qué label y categoría corresponden a este tag
    nicho_encontrado = None
    sub_encontrado = None
    pista = None
    for cat_label, cat_subs in SUBCATEGORIAS.items():
        for slabel, sval in cat_subs.items():
            if sub_tag(sval) == tag_buscado:
                nicho_encontrado = cat_label
                sub_encontrado = slabel
                pista = sub_pista(sval)
                break
        if sub_encontrado:
            break

    # Sentinel especial: pronósticos del día (no busca TikToks, genera video propio)
    if tag_buscado == "__pronosticos__":
        await query.edit_message_text("🔮 <b>Cargando partidos de hoy...</b>", parse_mode="HTML")
        await _generar_y_mostrar_pronosticos(ctx.bot)
        return

    await query.edit_message_text(f"🗂 Subcategoría: <b>{html.escape(tag_buscado)}</b>", parse_mode="HTML")

    # Si esta subcategoría tiene un tercer nivel, mostrarlo antes de ir al país
    if nicho_encontrado and sub_encontrado:
        sub_sub_map = SUB_SUBCATEGORIAS.get(nicho_encontrado, {}).get(sub_encontrado)
        if sub_sub_map:
            # Guardar filtro de antigüedad para subcategorías que lo requieren
            if sub_encontrado == "📅 Partidos recientes":
                estado.actualizar(max_horas_busqueda=48)
            await _preguntar_sub_subcategoria(ctx.bot, nicho_encontrado, sub_encontrado)
            return

    estado.actualizar(tag_busqueda=tag_buscado, pista_busqueda=pista)
    await _preguntar_pais(ctx.bot)


async def _preguntar_sub_subcategoria(bot: Bot, nicho_label: str, sub_label: str):
    """Muestra los botones del tercer nivel para una subcategoría con filtros adicionales."""
    sub_sub_map = SUB_SUBCATEGORIAS[nicho_label][sub_label]
    filas = []
    items = list(sub_sub_map.items())
    for i in range(0, len(items), 2):
        fila = []
        for sslabel, ssval in items[i:i + 2]:
            fila.append(InlineKeyboardButton(sslabel, callback_data=f"subsub_{sub_tag(ssval)}"))
        filas.append(fila)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🗂 <b>{html.escape(sub_label)}</b>\n¿Qué tipo de contenido querés buscar?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )
    estado.actualizar(estado="eligiendo_sub_subcategoria")


async def cb_sub_subcategoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler para botones del tercer nivel (pattern: ^subsub_)."""
    query = update.callback_query
    await query.answer()
    tag_buscado = query.data[7:]   # quita el prefijo "subsub_"

    # Buscar la pista asociada a este tag en SUB_SUBCATEGORIAS
    pista = None
    for cat_subs in SUB_SUBCATEGORIAS.values():
        for sub_subs in cat_subs.values():
            for ssval in sub_subs.values():
                if sub_tag(ssval) == tag_buscado:
                    pista = sub_pista(ssval)
                    break

    await query.edit_message_text(f"🗂 Filtro: <b>{html.escape(tag_buscado)}</b>", parse_mode="HTML")
    estado.actualizar(tag_busqueda=tag_buscado, pista_busqueda=pista)
    await _preguntar_pais(ctx.bot)


async def cb_pais(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler para cualquier botón de país (pattern: ^pais_)."""
    query = update.callback_query
    await query.answer()
    label = query.data[5:]   # quita "pais_"
    sufijo = PAISES.get(label)  # None = global
    await query.edit_message_text(
        f"🌍 País: <b>{html.escape(label)}</b>",
        parse_mode="HTML",
    )
    estado.actualizar(pais_busqueda=sufijo)
    await _preguntar_filtro_tiempo(ctx.bot)


async def cb_filtro_tiempo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler para el botón de filtro de antigüedad (pattern: ^filtrotiempo_)."""
    query = update.callback_query
    await query.answer()
    label = query.data[len("filtrotiempo_"):]
    horas = FILTROS_TIEMPO.get(label)
    await query.edit_message_text(
        f"⏳ Antigüedad: <b>{html.escape(label)}</b>",
        parse_mode="HTML",
    )
    estado.actualizar(max_horas_busqueda=horas)
    await _iniciar_busqueda(ctx.bot)


async def _preguntar_orden_clips(bot: Bot):
    """Pregunta al usuario si quiere reordenar los clips antes de compilar."""
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Claude elige el orden", callback_data="orden_claude")],
        [InlineKeyboardButton("🔢 Yo los ordeno",         callback_data="orden_manual")],
        [InlineKeyboardButton("📋 Así están bien",         callback_data="orden_ok")],
    ])
    datos = estado.leer()
    clips = datos.get("clips", [])
    lista = "\n".join(
        f"  {i+1}. {html.escape(c.get('titulo', 'Sin título')[:55])}"
        for i, c in enumerate(clips)
    )
    estado.actualizar(estado="reordenando_clips")
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"✅ <b>Clips aprobados.</b> ¿Cómo los ordenamos?\n\n"
            f"<b>Orden actual:</b>\n{lista}"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_aprobar_clips(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ <b>Clips aprobados.</b>", parse_mode="HTML")
    await _preguntar_orden_clips(ctx.bot)


def _ordenar_clips_con_claude(clips: list[dict]) -> list[int] | None:
    """
    Llama a Claude para determinar el orden óptimo de los clips.
    Retorna lista de índices 0-based en el orden sugerido, o None si falla.
    """
    import anthropic as _anthropic
    import json as _json

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    resumen = "\n".join(
        f"{i+1}. \"{c.get('titulo', 'Sin título')[:70]}\" "
        f"— {_fmt_dur(c.get('duracion', 0))} — {_fmt_vistas(c.get('vistas', 0))} vistas"
        for i, c in enumerate(clips)
    )
    prompt = (
        f"Tengo {len(clips)} clips de TikTok que voy a compilar en un video de YouTube Shorts.\n\n"
        f"{resumen}\n\n"
        f"Ordenálos para maximizar la retención del espectador:\n"
        f"- El primer clip debe ser el más llamativo o viral (engancha desde el primer segundo)\n"
        f"- Los del medio sostienen la atención\n"
        f"- El último deja ganas de más o tiene un buen cierre\n\n"
        f"Respondé SOLO con JSON: {{\"orden\": [n, n, n, ...]}} "
        f"donde cada n es el número de clip (1 a {len(clips)}). Incluí todos los clips exactamente una vez."
    )
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = _json.loads(raw.strip())
        orden = data["orden"]
        # Validate: must be a permutation of 1..len(clips)
        if sorted(orden) != list(range(1, len(clips) + 1)):
            return None
        return [n - 1 for n in orden]  # convert to 0-based
    except Exception:
        return None


async def _despues_de_ordenar_clips(bot: Bot):
    """
    Se llama al terminar de ordenar los clips de la búsqueda actual.
    Si el modo Versus está activo, guarda este lado y pasa al siguiente
    (o mergea todos los lados con puntos de transición y sigue a música
    cuando ya se completaron todos). Fuera de Versus, sigue a música normal.
    """
    datos = estado.leer()
    if not datos.get("vs_activo"):
        await _preguntar_musica(bot)
        return

    lado_actual = datos.get("vs_lado_actual", 0)
    total = datos.get("vs_total_lados", 2)
    nombre_lado = datos.get("tag_busqueda") or f"Lado {lado_actual + 1}"
    clips_lado = datos.get("clips", [])

    lados = datos.get("vs_lados", [])
    lados.append({"nombre": nombre_lado, "clips": clips_lado})

    if lado_actual + 1 < total:
        estado.actualizar(
            vs_lado_actual=lado_actual + 1,
            vs_lados=lados,
            clips=[],
            tag_busqueda=None,
            pista_busqueda=None,
            pais_busqueda=None,
            max_horas_busqueda=None,
            candidatos_pool=[],
        )
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ Lado {lado_actual + 1}/{total} listo: <b>{html.escape(str(nombre_lado))}</b>\n\n"
                f"Ahora elegí el lado {lado_actual + 2}/{total}:"
            ),
            parse_mode="HTML",
        )
        await _preguntar_categoria(bot)
        return

    # Último lado: mergear todos los bloques, tageando cada clip con su lado
    # para que el compilador pueda insertar la transición en el punto correcto.
    clips_final = []
    for i, lado in enumerate(lados):
        for c in lado["clips"]:
            clip = dict(c)
            clip["_vs_lado"] = i
            clips_final.append(clip)

    nombres = " 🆚 ".join(str(l["nombre"]) for l in lados)
    estado.actualizar(clips=clips_final, vs_lados=lados)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🆚 <b>Versus armado:</b> {html.escape(nombres)}\n\n{len(clips_final)} clips en total. Pasando a música...",
        parse_mode="HTML",
    )
    await _preguntar_musica(bot)


async def cb_orden_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Mantiene el orden actual y sigue a música (o al siguiente lado si es Versus)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📋 Orden mantenido.")
    await _despues_de_ordenar_clips(ctx.bot)


async def cb_orden_claude(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pide a Claude el orden óptimo y aplica el resultado."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🤖 Claude está analizando los clips...")

    datos = estado.leer()
    clips = datos.get("clips", [])

    orden = await asyncio.get_event_loop().run_in_executor(
        None, _ordenar_clips_con_claude, clips
    )

    if not orden:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="⚠️ No pude determinar el orden. Usando el orden actual.",
        )
        await _despues_de_ordenar_clips(ctx.bot)
        return

    clips_reordenados = [clips[i] for i in orden]
    estado.actualizar(clips=clips_reordenados)

    lista = "\n".join(
        f"  {i+1}. {html.escape(c.get('titulo', 'Sin título')[:55])}"
        for i, c in enumerate(clips_reordenados)
    )
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"✅ <b>Orden de Claude aplicado:</b>\n\n{lista}",
        parse_mode="HTML",
    )
    await _despues_de_ordenar_clips(ctx.bot)


def _teclado_orden_manual(clips: list[dict], restantes: list[int], posicion: int) -> InlineKeyboardMarkup:
    filas = []
    for i in range(0, len(restantes), 2):
        fila = []
        for j in range(2):
            if i + j < len(restantes):
                idx = restantes[i + j]
                titulo = clips[idx].get("titulo", f"Clip {idx+1}")[:30]
                fila.append(InlineKeyboardButton(
                    f"Clip {idx+1} — {titulo}",
                    callback_data=f"orden_pick_{idx}",
                ))
        filas.append(fila)
    return InlineKeyboardMarkup(filas)


async def cb_orden_manual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inicia el picker manual de orden."""
    query = update.callback_query
    await query.answer()

    datos = estado.leer()
    clips = datos.get("clips", [])
    restantes = list(range(len(clips)))

    estado.actualizar(
        orden_manual_seleccionado=[],
        orden_manual_restantes=restantes,
        estado="reordenando_manual",
    )

    teclado = _teclado_orden_manual(clips, restantes, 0)
    await query.edit_message_text(
        "🔢 <b>¿Cuál va primero?</b>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_orden_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """El usuario elige un clip para la posición actual."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[-1])
    datos = estado.leer()
    clips = datos.get("clips", [])
    seleccionado = datos.get("orden_manual_seleccionado", [])
    restantes = datos.get("orden_manual_restantes", [])

    if idx not in restantes:
        await query.answer("Ya elegiste ese clip.", show_alert=True)
        return

    seleccionado.append(idx)
    restantes.remove(idx)
    estado.actualizar(orden_manual_seleccionado=seleccionado, orden_manual_restantes=restantes)

    titulo_elegido = clips[idx].get("titulo", f"Clip {idx+1}")[:40]
    posicion = len(seleccionado)

    if not restantes:
        # Todos elegidos — aplicar orden y seguir
        clips_reordenados = [clips[i] for i in seleccionado]
        estado.actualizar(clips=clips_reordenados)
        lista = "\n".join(
            f"  {i+1}. {html.escape(c.get('titulo', 'Sin título')[:55])}"
            for i, c in enumerate(clips_reordenados)
        )
        await query.edit_message_text(
            f"✅ <b>Orden definido:</b>\n\n{lista}",
            parse_mode="HTML",
        )
        await _despues_de_ordenar_clips(ctx.bot)
        return

    ordinal = ["segundo", "tercero", "cuarto", "quinto", "sexto"][min(posicion - 1, 5)]
    teclado = _teclado_orden_manual(clips, restantes, posicion)
    await query.edit_message_text(
        f"✅ <b>Posición {posicion}:</b> {html.escape(titulo_elegido)}\n\n"
        f"🔢 <b>¿Cuál va {ordinal}?</b>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_buscar_otros(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Cambiando categoría...")
    estado.actualizar(estado="idle")
    await _preguntar_categoria(ctx.bot)


async def cb_ampliar_filtro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reintenta la misma búsqueda con una ventana de fecha más amplia (pattern: ^ampliarfiltro_)."""
    query = update.callback_query
    await query.answer()
    sufijo = query.data[len("ampliarfiltro_"):]
    horas = None if sufijo == "none" else int(sufijo)
    await query.edit_message_text(f"🔎 Ampliando búsqueda a {_label_filtro_horas(horas)}...")
    estado.actualizar(max_horas_busqueda=horas)
    await _iniciar_busqueda(ctx.bot)


async def cb_cambiar_clip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reemplaza un clip individual por el siguiente candidato del pool."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("_")[-1]) - 1   # "cambiar_2" → índice 1
    datos = estado.leer()
    clips = datos.get("clips", [])
    pool = datos.get("candidatos_pool", [])

    if idx >= len(clips):
        await query.edit_message_text("❌ Clip no encontrado.")
        return

    if not pool:
        await query.edit_message_text(
            "⚠️ No quedan más candidatos en el pool.\n"
            "Tocá <b>🔄 Buscar otros</b> para buscar clips nuevos.",
            parse_mode="HTML",
        )
        return

    reemplazado = clips[idx].get("titulo", f"Clip {idx+1}")[:40]
    nuevo = pool.pop(0)
    clips[idx] = nuevo

    estado.actualizar(clips=clips, candidatos_pool=pool)
    await query.edit_message_text(
        f'🔄 <i>"{html.escape(reemplazado)}"</i> reemplazado. Mostrando clips actualizados...',
        parse_mode="HTML",
    )
    await _enviar_clips_para_aprobacion(ctx.bot, clips)


async def cb_aprobar_seo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "✅ SEO aprobado. Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_subir_ahora(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("⚡ Publicando ahora...")
    await _iniciar_subida(ctx.bot, fecha_publicacion=None)


async def cb_subir_programado(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    rec = datos.get("recomendacion_horario") or {}
    fecha = rec.get("iso_utc")
    label = rec.get("label", "la hora recomendada")
    await query.edit_message_text(f"📅 Programando para {html.escape(label)}...")
    await _iniciar_subida(ctx.bot, fecha_publicacion=fecha)


async def cb_elegir_hora(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ <b>Escribí cuándo querés publicar</b>\n\n"
        "Podés usar cualquier formato, por ejemplo:\n"
        "• <code>mañana a las 20:00</code>\n"
        "• <code>viernes 18:30</code>\n"
        "• <code>lunes a las 9</code>\n"
        "• <code>2026-05-10 19:00</code>",
        parse_mode="HTML",
    )
    estado.actualizar(estado="esperando_hora_custom")


async def cb_hora_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Selecciona una opción de horario predefinida."""
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    datos = estado.leer()
    opciones = datos.get("opciones_horario", [])
    if idx >= len(opciones):
        await query.edit_message_text("❌ Opción no disponible. Usá /trabajar para reiniciar.")
        return
    op = opciones[idx]
    estado.actualizar(recomendacion_horario={"label": op["label"], "iso_utc": op["iso_utc"]})
    await query.edit_message_text(f"📅 Programando para <b>{html.escape(op['label'])}</b>...", parse_mode="HTML")
    await _iniciar_subida(ctx.bot, fecha_publicacion=op["iso_utc"])


async def cb_volver_seo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Vuelve al preview + SEO desde el selector de horario."""
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    preview = datos.get("video_preview") or datos.get("video_compilado", "")
    seo = datos.get("seo") or {}
    thumbnail = datos.get("thumbnail")
    video_horizontal = datos.get("video_horizontal")
    if not preview or not seo:
        await query.edit_message_text("❌ No hay preview guardado. Iniciá de nuevo con /trabajar.")
        return
    await query.edit_message_text("⬅️ Volviendo al preview...")
    estado.actualizar(estado="aprobacion_preview")
    await _enviar_preview_y_seo(ctx.bot, preview, seo, thumbnail, video_horizontal)


async def handle_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Captura mensajes de texto según el estado activo."""
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    est = datos.get("estado")
    texto = update.message.text.strip()

    if est == "txt_esperando_tema":
        tema = texto.strip()
        datos = estado.leer()
        tipo = datos.get("txt_tipo", "curiosidad")
        estado.actualizar(txt_tema=tema)
        await update.message.reply_text(
            f"✅ Tema: <b>{html.escape(tema)}</b>",
            parse_mode="HTML",
        )
        await _generar_y_mostrar_texto(ctx.bot, tipo, tema)
        return

    if est == "img_esperando_tema":
        tema = texto.strip()
        estado.actualizar(img_tema=tema)
        await update.message.reply_text(
            f"✅ Tema: <b>{html.escape(tema)}</b>",
            parse_mode="HTML",
        )
        await _generar_y_mostrar_pregunta(ctx.bot, tema)
        return

    if est == "img_esperando_cancion":
        msg = await update.message.reply_text(
            f"🔍 Buscando <b>{html.escape(texto)}</b> en YouTube...",
            parse_mode="HTML",
        )
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, buscar_canciones, texto, 5)
        if not results:
            await msg.edit_text(
                "❌ No encontré esa canción. Intentá con otro nombre.\n"
                "O elegí una de la lista:",
                parse_mode="HTML",
                reply_markup=_teclado_musica_imagen(),
            )
            estado.actualizar(estado="img_eligiendo_musica")
            return
        estado.actualizar(
            canciones_resultados=results,
            cancion_idx=0,
            estado="img_eligiendo_cancion",
        )
        await msg.edit_text(
            f"✅ Encontré <b>{len(results)} resultados</b>. Escuchá el preview:",
            parse_mode="HTML",
        )
        await _mostrar_cancion_imagen(ctx.bot, idx=0)
        return

    if est == "elc_esperando_cancion":
        msg = await update.message.reply_text(
            f"🔍 Buscando <b>{html.escape(texto)}</b> en YouTube...",
            parse_mode="HTML",
        )
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, buscar_canciones, texto, 5)
        if not results:
            await msg.edit_text(
                "❌ No encontré esa canción. Intentá con otro nombre.\n"
                "O elegí una de la lista:",
                parse_mode="HTML",
                reply_markup=_teclado_musica_eleccion(),
            )
            estado.actualizar(estado="elc_eligiendo_musica")
            return
        estado.actualizar(
            canciones_resultados=results,
            cancion_idx=0,
            estado="elc_eligiendo_cancion",
        )
        await msg.edit_text(
            f"✅ Encontré <b>{len(results)} resultados</b>. Escuchá el preview:",
            parse_mode="HTML",
        )
        await _mostrar_cancion_eleccion(ctx.bot, idx=0)
        return

    if est == "nar_esperando_cancion":
        msg = await update.message.reply_text(
            f"🔍 Buscando <b>{html.escape(texto)}</b> en YouTube...",
            parse_mode="HTML",
        )
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, buscar_canciones, texto, 5)
        if not results:
            await msg.edit_text(
                "❌ No encontré esa canción. Intentá con otro nombre.\n"
                "O elegí una de la lista:",
                parse_mode="HTML",
                reply_markup=_teclado_musica_narracion(),
            )
            estado.actualizar(estado="nar_eligiendo_musica")
            return
        estado.actualizar(
            canciones_resultados=results,
            cancion_idx=0,
            estado="nar_eligiendo_cancion",
        )
        await msg.edit_text(
            f"✅ Encontré <b>{len(results)} resultados</b>. Escuchá el preview:",
            parse_mode="HTML",
        )
        await _mostrar_cancion_narracion(ctx.bot, idx=0)
        return

    if est == "img_eligiendo_musica":
        await update.message.reply_text(
            "👆 Usá los botones de arriba para elegir música.\n"
            "Si querés buscar una canción específica, tocá <b>🔍 Buscar canción en YouTube</b>.",
            parse_mode="HTML",
        )
        return

    if est == "esperando_nombre_cancion":
        msg = await update.message.reply_text(
            f"🔍 Buscando <b>{html.escape(texto)}</b> en YouTube...",
            parse_mode="HTML",
        )
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, buscar_canciones, texto, 5)
        if not results:
            await msg.edit_text(
                "❌ No encontré esa canción. Intentá con otro nombre o artista.",
                parse_mode="HTML",
            )
            return
        estado.actualizar(
            canciones_resultados=results,
            cancion_idx=0,
            estado="eligiendo_cancion",
        )
        await msg.edit_text(
            f"✅ Encontré <b>{len(results)} resultados</b>. Escuchá el preview:",
            parse_mode="HTML",
        )
        await _mostrar_cancion(ctx.bot, idx=0)
        return

    if est == "esperando_keyword":
        # Preservar espacios — se usan como keyword en feed/search
        keyword = texto.strip()
        await update.message.reply_text(
            f"✍️ Tema: <b>{html.escape(keyword)}</b>",
            parse_mode="HTML",
        )
        estado.actualizar(tag_busqueda=keyword)
        await _preguntar_pais(ctx.bot)
        return

    if est == "esperando_urls":
        # El usuario pegó una o más URLs de TikTok
        import re
        urls = re.findall(r'https?://\S+', texto)
        if not urls:
            await update.message.reply_text(
                "❌ No encontré ningún link válido. Pegá URLs que empiecen con <code>https://</code>",
                parse_mode="HTML",
            )
            return
        msg = await update.message.reply_text(f"🔍 Obteniendo info de {len(urls)} video(s)...")
        loop = asyncio.get_event_loop()
        clips = await loop.run_in_executor(None, info_desde_urls, urls)
        if not clips:
            await msg.edit_text("❌ No pude obtener info de esas URLs. Revisá que sean links válidos de TikTok.")
            return
        estado.actualizar(estado="aprobacion_clips", clips=clips)
        await msg.edit_text(f"✅ ¡Encontré {len(clips)} clip(s)! Revisalos:")
        await _enviar_clips_para_aprobacion(ctx.bot, clips)
        return

    if est == "esperando_tiktok":
        # El usuario comparte videos de TikTok de a uno desde la app.
        import re
        urls = re.findall(r'https?://\S+', texto)
        if not urls:
            await update.message.reply_text(
                "❌ No encontré ningún link ahí. Compartí el video de nuevo desde TikTok."
            )
            return

        loop = asyncio.get_event_loop()
        nuevos = await loop.run_in_executor(None, info_desde_urls, urls)
        if not nuevos:
            await update.message.reply_text(
                "❌ No pude obtener info de ese video. Probá compartirlo de nuevo."
            )
            return

        pendientes = datos.get("tiktok_pendientes", [])
        pendientes.extend(nuevos)
        estado.actualizar(tiktok_pendientes=pendientes)

        etiqueta_lado = ""
        if datos.get("vs_activo"):
            lado_actual = datos.get("vs_lado_actual", 0) + 1
            total = datos.get("vs_total_lados", 2)
            etiqueta_lado = f" del Lado {lado_actual}"
            texto_listo = f"✔️ Lado {lado_actual}/{total} listo ({len(pendientes)})"
        else:
            texto_listo = f"🎬 Compilar ({len(pendientes)})"

        texto_conf = (
            f"✅ Video {len(pendientes)}{etiqueta_lado} agregado."
            if len(nuevos) == 1
            else f"✅ {len(nuevos)} videos agregados{etiqueta_lado} (total: {len(pendientes)})."
        )
        teclado = InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Agregar más", callback_data="tiktok_agregar_mas"),
            InlineKeyboardButton(texto_listo,      callback_data="tiktok_listo"),
        ]])

        msg_id = datos.get("tiktok_msg_id")
        if msg_id:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=CHAT_ID, message_id=msg_id,
                    text=texto_conf, reply_markup=teclado,
                )
                return
            except Exception:
                pass  # el mensaje anterior no se pudo editar (ej. borrado) — mandamos uno nuevo

        msg = await update.message.reply_text(texto_conf, reply_markup=teclado)
        estado.actualizar(tiktok_msg_id=msg.message_id)
        return

    if est != "esperando_hora_custom":
        return   # ignorar mensajes en otros estados
    msg = await update.message.reply_text("⏳ Interpretando la fecha...")

    loop = asyncio.get_event_loop()
    iso_utc = await loop.run_in_executor(None, _parsear_hora_custom, texto)

    if not iso_utc:
        await msg.edit_text(
            "❌ No pude interpretar esa fecha. Intentá de nuevo con otro formato:\n"
            "Ej: <code>viernes 18:00</code> o <code>mañana a las 20:00</code>",
            parse_mode="HTML",
        )
        return   # quedamos en estado esperando_hora_custom para reintentar

    # Mostrar confirmación con la fecha parseada
    import pytz as _pytz
    from datetime import datetime as _dt
    tz_arg = _pytz.timezone("America/Argentina/Buenos_Aires")
    dt_arg = _dt.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_pytz.utc).astimezone(tz_arg)
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    label = f"{dias_es[dt_arg.weekday()].capitalize()} {dt_arg.strftime('%d/%m')} a las {dt_arg.strftime('%H:%M')}"

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Confirmar: {label}", callback_data="subir_custom_ok"),
        InlineKeyboardButton("✏️ Cambiar", callback_data="elegir_hora"),
    ]])
    estado.actualizar(estado="aprobacion_horario_custom", recomendacion_horario={
        "label": label, "iso_utc": iso_utc, "razon": f"Hora elegida por vos: {label}"
    })
    await msg.edit_text(
        f"📅 ¿Confirmas publicar el <b>{html.escape(label)}</b>?",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_subir_custom_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    rec = datos.get("recomendacion_horario") or {}
    fecha = rec.get("iso_utc")
    label = rec.get("label", "")
    await query.edit_message_text(f"📅 Programando para {html.escape(label)}...")
    await _iniciar_subida(ctx.bot, fecha_publicacion=fecha)


async def cb_recompilar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Vuelve a buscar clips nuevos manteniendo el formato elegido."""
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🔄 Buscando clips nuevos...")
    estado.actualizar(estado="idle")
    await _iniciar_busqueda(ctx.bot)


async def cb_regenerar_seo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✏️ Regenerando SEO...")

    datos = estado.leer()
    video = datos.get("video_compilado")
    preview = datos.get("video_preview") or video
    if not video or not Path(video).exists():
        estado.reset()
        await ctx.bot.send_message(chat_id=CHAT_ID, text="❌ Video no encontrado. Iniciá con /trabajar.")
        return

    loop = asyncio.get_event_loop()

    from modules.seo_gen import generar_seo, extraer_frames_thumbnail

    def _regen():
        frames = extraer_frames_thumbnail(video, cantidad=4)
        clips_d = datos.get("clips_descargados") or datos.get("clips", [])
        titulos = ", ".join(c.get("titulo", "")[:40] for c in clips_d)
        return generar_seo(
            descripcion=f"Compilación de videos virales: {titulos}",
            tipo="Compilación viral de humor",
            canal_handle="",
            idioma="Español",
            frames_b64=frames if frames else None,
        )

    nuevo_seo = await loop.run_in_executor(None, _regen)
    estado.actualizar(seo=nuevo_seo)
    thumbnail = datos.get("thumbnail")
    video_horizontal = datos.get("video_horizontal")
    await _enviar_preview_y_seo(ctx.bot, preview, nuevo_seo, thumbnail, video_horizontal)


# ── /texto — Text-on-Video Shorts ─────────────────────────────────────────────

async def cmd_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inicia el flujo de creación de un Short de texto sobre video."""
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return

    labels = list(TIPOS_TEXTO.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [InlineKeyboardButton(label, callback_data=f"txt_tipo_{TIPOS_TEXTO[label]}")
                for label in labels[i:i+2]]
        filas.append(fila)

    await update.message.reply_text(
        "✍️ <b>Shorts de texto</b>\n\n"
        "¿Qué tipo de contenido querés crear?\n"
        "<i>Claude va a generar el texto, vos elegís el fondo y la música.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )
    estado.actualizar(estado="txt_eligiendo_tipo")


async def cb_txt_tipo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tipo = query.data[len("txt_tipo_"):]
    label = next((k for k, v in TIPOS_TEXTO.items() if v == tipo), tipo)
    estado.actualizar(txt_tipo=tipo)
    await query.edit_message_text(
        f"✅ Tipo: <b>{html.escape(label)}</b>\n\n"
        "✍️ <b>¿Sobre qué tema?</b>\n\n"
        "Escribí el tema (ej: <code>Messi en el Mundial 2022</code>, <code>los mejores goles de chilena</code>, "
        "<code>datos que no sabías del fútbol argentino</code>)\n\n"
        "O mandá /saltar para que la IA elija.",
        parse_mode="HTML",
    )
    estado.actualizar(estado="txt_esperando_tema")


async def _generar_y_mostrar_texto(bot: Bot, tipo: str, tema: str):
    """Llama a Claude, genera el texto y lo envía al usuario para aprobación."""
    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="🤖 <b>Claude está generando el texto...</b>",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    try:
        datos_texto = await loop.run_in_executor(None, generar_texto_claude, tipo, tema)
    except Exception as e:
        await msg.edit_text(f"❌ Error generando texto: {e}")
        estado.reset()
        return

    estado.actualizar(txt_datos=datos_texto, estado="txt_aprobando_texto")

    titulo = datos_texto.get("titulo", "")
    texto = datos_texto.get("texto", "")
    hashtags = " ".join(datos_texto.get("hashtags", []))

    preview = texto[:600] + ("..." if len(texto) > 600 else "")
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Aprobar texto", callback_data="txt_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",     callback_data="txt_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="txt_cancelar")],
    ])
    await msg.edit_text(
        f"📝 <b>Texto generado</b>\n\n"
        f"🎯 <b>Título:</b> {html.escape(titulo)}\n\n"
        f"📄 <b>Texto:</b>\n<i>{html.escape(preview)}</i>\n\n"
        f"🏷 <b>Hashtags:</b> {html.escape(hashtags)}",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_txt_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Texto aprobado. ¿Qué fondo querés?")

    fondos_list = list(FONDOS.keys())
    filas = []
    for i in range(0, len(fondos_list), 2):
        fila = []
        for j in range(2):
            if i + j < len(fondos_list):
                label = fondos_list[i + j]
                fila.append(InlineKeyboardButton(label, callback_data=f"txt_fondo_{i+j}"))
        filas.append(fila)

    estado.actualizar(estado="txt_eligiendo_fondo")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text="🎨 <b>¿Qué fondo querés?</b>\n<i>Se genera al instante, sin descargas.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_txt_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando texto...")
    datos = estado.leer()
    await _generar_y_mostrar_texto(ctx.bot, datos.get("txt_tipo", "curiosidad"), datos.get("txt_tema", ""))


async def cb_txt_fondo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("txt_fondo_"):])
    fondos_list = list(FONDOS.keys())
    if idx >= len(fondos_list):
        await query.edit_message_text("❌ Opción no válida.")
        return

    estilo_key = fondos_list[idx]
    # Guardar el estilo elegido y pasar directo a música (no hay descarga ni generación previa)
    estado.actualizar(txt_fondo_label=estilo_key, txt_fondo_ruta=estilo_key, estado="txt_eligiendo_musica")
    await query.edit_message_text(
        f"✅ Fondo <b>{html.escape(estilo_key)}</b> seleccionado.",
        parse_mode="HTML",
    )

    # Reutilizar el selector de música existente
    tracks = listar_tracks()
    filas = [[InlineKeyboardButton("🔇 Sin música", callback_data="txt_sin_musica")]]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"txt_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"txt_musica_{i+1}"))
        filas.append(fila)

    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text="✅ Fondo listo.\n\n🎵 <b>¿Agregás música de fondo?</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def _crear_y_enviar_video_texto(bot: Bot, musica_ruta: str | None):
    """Crea el video con PIL+FFmpeg y lo envía para aprobación."""
    datos      = estado.leer()
    txt_datos  = datos.get("txt_datos", {})
    texto      = txt_datos.get("texto", "")
    titulo     = txt_datos.get("titulo", "")
    estilo_key = datos.get("txt_fondo_ruta", "⬛ Negro")  # guardamos el estilo como "ruta"

    if not texto:
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /texto para empezar de nuevo.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⚙️ <b>Creando el video...</b>\n<i>Esto tarda ~20-40 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_out = tempfile.mkdtemp(prefix="ytbot_txt_out_")
    output_path = os.path.join(carpeta_out, "texto_video.mp4")
    loop = asyncio.get_event_loop()

    inicio_txt = asyncio.get_event_loop().time()
    async def _ping_txt():
        pasos = ["✍️ Renderizando texto...", "🎵 Mezclando audio...", "📦 Finalizando..."]
        i = 0
        while True:
            await asyncio.sleep(30)
            elapsed = int(asyncio.get_event_loop().time() - inicio_txt)
            label = pasos[min(i, len(pasos)-1)]
            try:
                await msg.edit_text(
                    f"{label}\n<i>({elapsed}s transcurridos, por favor esperá...)</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            i += 1

    ping_task_txt = asyncio.create_task(_ping_txt())
    try:
        ok, err = await loop.run_in_executor(
            None, crear_video_texto,
            texto, estilo_key, output_path, titulo, musica_ruta, 0.18, SECS_POR_PAGINA_TEXTO,
        )
    except Exception as e:
        ping_task_txt.cancel()
        await msg.edit_text(f"❌ Error inesperado creando el video:\n<code>{html.escape(str(e)[:300])}</code>", parse_mode="HTML")
        estado.reset()
        return
    ping_task_txt.cancel()

    if not ok:
        await msg.edit_text(f"❌ Error creando el video:\n<code>{html.escape(err[:300])}</code>", parse_mode="HTML")
        estado.reset()
        return

    estado.actualizar(txt_video_path=output_path, estado="txt_aprobando_video")

    hashtags = " ".join(txt_datos.get("hashtags", ["#Shorts"]))
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="txt_subir"),
            InlineKeyboardButton("🗑 Descartar",       callback_data="txt_descartar"),
        ],
        [InlineKeyboardButton("🎙️ Agregar voz intro", callback_data="agregar_voz_intro")],
    ])

    from pathlib import Path as _Path
    size_mb = _Path(output_path).stat().st_size / (1024 * 1024)
    await msg.edit_text("✅ <b>Video listo.</b> Enviando preview...", parse_mode="HTML")

    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption=f"🎬 <b>{html.escape(titulo)}</b>\n{html.escape(hashtags)}",
                    parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120,
                    read_timeout=120,
                )
            return
        except Exception:
            pass

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🎬 <b>{html.escape(titulo)}</b>\n{html.escape(hashtags)}\n\n<i>(Video muy grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_txt_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("txt_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(txt_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b> seleccionada.", parse_mode="HTML")
    await _crear_y_enviar_video_texto(ctx.bot, track["ruta"])


async def cb_txt_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música de fondo.")
    await _crear_y_enviar_video_texto(ctx.bot, None)


async def cb_txt_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    video_path = datos.get("txt_video_path", "")
    txt_datos = datos.get("txt_datos", {})

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Usá /texto para empezar de nuevo.")
        estado.reset()
        return

    hashtags = " ".join(txt_datos.get("hashtags", ["#Shorts"]))
    seo = {
        "titulo": txt_datos.get("titulo", "Short viral"),
        "descripcion": f"{txt_datos.get('texto', '')[:300]}\n\n{hashtags}",
        "tags": [h.lstrip("#") for h in txt_datos.get("hashtags", [])],
    }
    estado.actualizar(video_compilado=video_path, video_preview=video_path, seo=seo)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_txt_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Video descartado.")
    estado.reset()


async def cb_txt_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


# ── /imagen — Reel de pregunta viral con imágenes ─────────────────────────────

_TEMAS_IMAGEN = [
    ("⚽ Messi vs Ronaldo",       "Messi o Ronaldo, el mejor de la historia del fútbol"),
    ("🏆 Mejor equipo histórico", "el mejor equipo de fútbol de todos los tiempos"),
    ("🇦🇷 Fútbol argentino",     "fútbol argentino y la selección nacional"),
    ("🌍 Mundial 2026",           "el Mundial 2026 y los favoritos para ganarlo"),
    ("🥅 Goleadores históricos",  "los mejores goleadores del fútbol mundial"),
    ("💪 Mejor jugador joven",    "el mejor jugador joven del momento en el fútbol"),
    ("🔥 Champions League",       "la Champions League y los grandes clubes europeos"),
    ("🤔 Mejores técnicos",       "los mejores entrenadores de fútbol de la historia"),
]


def _teclado_temas_imagen() -> InlineKeyboardMarkup:
    filas = []
    for i in range(0, len(_TEMAS_IMAGEN), 2):
        fila = [InlineKeyboardButton(_TEMAS_IMAGEN[i][0], callback_data=f"img_tema_{i}")]
        if i + 1 < len(_TEMAS_IMAGEN):
            fila.append(InlineKeyboardButton(_TEMAS_IMAGEN[i + 1][0], callback_data=f"img_tema_{i + 1}"))
        filas.append(fila)
    filas.append([
        InlineKeyboardButton("🎲 Sorprendeme",      callback_data="img_tema_random"),
        InlineKeyboardButton("✍️ Escribir tema",    callback_data="img_tema_escribir"),
    ])
    return InlineKeyboardMarkup(filas)


async def _mostrar_menu_temas_imagen(bot: Bot):
    estado.actualizar(estado="img_eligiendo_tema")
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🖼 <b>Reel de pregunta viral</b>\n\n"
            "Claude genera una pregunta polémica y busca 6 imágenes relacionadas.\n\n"
            "¿Sobre qué tema?"
        ),
        parse_mode="HTML",
        reply_markup=_teclado_temas_imagen(),
    )


async def cmd_imagen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return
    await _mostrar_menu_temas_imagen(ctx.bot)


async def cb_img_tema(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tema elegido por botón (img_tema_0, img_tema_1, ...)."""
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("img_tema_"):])
    label, tema_str = _TEMAS_IMAGEN[idx]
    estado.actualizar(img_tema=tema_str)
    await query.edit_message_text(
        f"✅ Tema: <b>{html.escape(label)}</b>\n\n🤖 Generando pregunta...",
        parse_mode="HTML",
    )
    await _generar_y_mostrar_pregunta(ctx.bot, tema_str)


async def cb_img_tema_random(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(img_tema="")
    await query.edit_message_text("🎲 <b>Eligiendo tema al azar...</b>", parse_mode="HTML")
    await _generar_y_mostrar_pregunta(ctx.bot, "")


async def cb_img_tema_escribir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="img_esperando_tema")
    await query.edit_message_text(
        "✍️ <b>Escribí el tema que querés</b>\n\n"
        "<i>Ej: amor y dinero, viajes, tecnología, deportes...</i>",
        parse_mode="HTML",
    )


async def _generar_y_mostrar_pregunta(bot: Bot, tema: str):
    """Llama a Claude, genera la pregunta y la muestra para aprobación."""
    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="🤖 <b>Claude está generando la pregunta...</b>",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    try:
        datos_ia = await loop.run_in_executor(None, generar_pregunta_claude, tema)
    except Exception as e:
        await msg.edit_text(f"❌ Error generando la pregunta: {e}")
        estado.reset()
        return

    estado.actualizar(img_datos=datos_ia, estado="img_aprobando_pregunta")
    pregunta = datos_ia.get("pregunta", "")
    titulo   = datos_ia.get("titulo", "")
    terminos = datos_ia.get("terminos", [])
    hashtags = " ".join(datos_ia.get("hashtags", []))

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta pregunta", callback_data="img_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",          callback_data="img_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="img_cancelar")],
    ])
    await msg.edit_text(
        f"❓ <b>Pregunta:</b> {html.escape(pregunta)}\n"
        f"📌 <b>Título YouTube:</b> {html.escape(titulo)}\n\n"
        f"🏷 <b>Hashtags:</b> {html.escape(hashtags)}",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_img_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    img_datos = datos.get("img_datos", {})
    terminos = img_datos.get("terminos", [])
    pregunta = img_datos.get("pregunta", "")

    await query.edit_message_text(
        f"✅ Pregunta: <b>{html.escape(pregunta)}</b>\n\n"
        "📥 <b>Descargando imágenes...</b>\n"
        "<i>Esto puede tardar 30-60 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_img_")
    loop = asyncio.get_event_loop()
    try:
        imagenes = await loop.run_in_executor(None, descargar_imagenes, terminos, carpeta_tmp)
    except Exception as e:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error descargando imágenes: <code>{html.escape(str(e)[:200])}</code>\n\nUsá /cancelar y empezá de nuevo.",
            parse_mode="HTML",
        )
        estado.reset()
        return

    if len(imagenes) < 3:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ No se pudieron descargar suficientes imágenes.\n"
                "Verificá que tengas <code>PEXELS_API_KEY</code> o "
                "<code>PIXABAY_API_KEY</code> en el archivo <code>.env</code>.\n\n"
                "Registrate gratis en pexels.com/api o pixabay.com/api"
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(img_imagenes=imagenes, img_carpeta=carpeta_tmp, estado="img_eligiendo_musica")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"✅ {len(imagenes)} imágenes descargadas.\n\n🎵 <b>¿Agregás música de fondo?</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_imagen(),
    )


def _teclado_musica_imagen() -> InlineKeyboardMarkup:
    tracks = listar_tracks()
    filas = [
        [InlineKeyboardButton("🔍 Buscar canción en YouTube", callback_data="img_buscar_cancion")],
        [InlineKeyboardButton("🔇 Sin música", callback_data="img_sin_musica")],
    ]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"img_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"img_musica_{i+1}"))
        filas.append(fila)
    return InlineKeyboardMarkup(filas)


async def cb_img_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando pregunta...")
    datos = estado.leer()
    await _generar_y_mostrar_pregunta(ctx.bot, datos.get("img_tema", ""))


async def cb_img_buscar_cancion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="img_esperando_cancion")
    await query.edit_message_text(
        "🔍 <b>¿Qué canción querés buscar?</b>\n"
        "Escribí el nombre o el artista:",
        parse_mode="HTML",
    )


async def cb_img_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("img_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(img_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b> seleccionada.", parse_mode="HTML")
    await _crear_y_enviar_reel_imagen(ctx.bot, track["ruta"])


async def cb_img_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música de fondo.")
    await _crear_y_enviar_reel_imagen(ctx.bot, None)


async def _crear_y_enviar_reel_imagen(bot: Bot, musica_ruta: str | None):
    """Crea el reel con FFmpeg y lo envía para aprobación."""
    datos = estado.leer()
    img_datos = datos.get("img_datos", {})
    pregunta = img_datos.get("pregunta", "")
    imagenes = datos.get("img_imagenes", [])

    if not pregunta or not imagenes:
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /imagen para empezar de nuevo.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⚙️ <b>Creando el reel...</b>\n<i>Esto tarda ~60-90 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_out = tempfile.mkdtemp(prefix="ytbot_img_out_")
    output_path = os.path.join(carpeta_out, "imagen_reel.mp4")
    loop = asyncio.get_event_loop()

    # Notificación periódica cada 30s para saber que sigue trabajando
    inicio = asyncio.get_event_loop().time()
    async def _ping_progreso():
        pasos = ["⚙️ Procesando clips...", "🎞 Aplicando transiciones...", "✍️ Agregando texto...", "🎵 Mezclando audio..."]
        i = 0
        while True:
            await asyncio.sleep(30)
            elapsed = int(asyncio.get_event_loop().time() - inicio)
            label = pasos[min(i, len(pasos)-1)]
            try:
                await msg.edit_text(
                    f"{label}\n<i>({elapsed}s transcurridos, por favor esperá...)</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            i += 1

    ping_task = asyncio.create_task(_ping_progreso())
    try:
        ok, err = await loop.run_in_executor(
            None, crear_reel_imagenes,
            imagenes, pregunta, output_path, musica_ruta, 0.18,
        )
    except Exception as e:
        ping_task.cancel()
        await msg.edit_text(
            f"❌ Error inesperado creando el reel:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return
    ping_task.cancel()

    if not ok:
        await msg.edit_text(
            f"❌ Error creando el reel:\n<code>{html.escape(err[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(img_video_path=output_path, estado="img_aprobando_video")

    hashtags = " ".join(img_datos.get("hashtags", ["#Shorts"]))
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="img_subir"),
            InlineKeyboardButton("🗑 Descartar",       callback_data="img_descartar"),
        ],
        [InlineKeyboardButton("🎙️ Agregar voz intro", callback_data="agregar_voz_intro")],
    ])

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    await msg.edit_text("✅ <b>Reel listo.</b> Enviando preview...", parse_mode="HTML")

    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption=f"❓ <b>{html.escape(pregunta)}</b>\n{html.escape(hashtags)}",
                    parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120,
                    read_timeout=120,
                )
            return
        except Exception:
            pass

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"❓ <b>{html.escape(pregunta)}</b>\n{html.escape(hashtags)}\n\n<i>(Video muy grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_img_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    video_path = datos.get("img_video_path", "")
    img_datos = datos.get("img_datos", {})

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Usá /imagen para empezar de nuevo.")
        estado.reset()
        return

    hashtags = " ".join(img_datos.get("hashtags", ["#Shorts"]))
    pregunta = img_datos.get("pregunta", "Short viral")
    titulo   = img_datos.get("titulo") or pregunta[:60]
    seo = {
        "titulo": titulo[:100],
        "descripcion": f"{pregunta}\n\n{hashtags}",
        "tags": [h.lstrip("#") for h in img_datos.get("hashtags", [])],
    }
    estado.actualizar(video_compilado=video_path, video_preview=video_path, seo=seo)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def _mostrar_cancion_imagen(bot: Bot, idx: int):
    """Descarga preview de la canción y muestra botones para el flujo /imagen."""
    datos = estado.leer()
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No hay más resultados.",
            reply_markup=_teclado_musica_imagen(),
        )
        estado.actualizar(estado="img_eligiendo_musica")
        return

    cancion = results[idx]
    titulo = cancion["titulo"]
    estado.actualizar(cancion_idx=idx)

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=f"⏳ <b>Descargando preview {idx + 1}/{len(results)}...</b>",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    preview_path = await loop.run_in_executor(None, descargar_preview, cancion["url"], 25)

    if preview_path:
        try:
            with open(preview_path, "rb") as f:
                await bot.send_audio(
                    chat_id=CHAT_ID, audio=f,
                    title=titulo[:64],
                    performer=f"Preview 25s · resultado {idx+1}/{len(results)}",
                    duration=25,
                )
            Path(preview_path).unlink(missing_ok=True)
        except Exception:
            pass

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta", callback_data="img_cancion_usar"),
            InlineKeyboardButton("⏭ Siguiente", callback_data="img_cancion_siguiente"),
        ],
        [InlineKeyboardButton("❌ Cancelar búsqueda", callback_data="img_buscar_cancion_volver")],
    ])
    await msg.edit_text(
        f"🎵 <b>{html.escape(titulo[:80])}</b>\n"
        f"<i>Resultado {idx+1}/{len(results)}</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_img_cancion_siguiente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0) + 1
    await query.edit_message_text("⏭ Cargando siguiente...")
    await _mostrar_cancion_imagen(ctx.bot, idx)


async def cb_img_cancion_usar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await query.edit_message_text("❌ Error. Volvé a elegir música.")
        estado.actualizar(estado="img_eligiendo_musica")
        return

    cancion = results[idx]
    await query.edit_message_text(
        f"⏳ <b>Descargando:</b> {html.escape(cancion['titulo'][:60])}...",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, descargar_desde_url, cancion["url"])
    if not result:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No se pudo descargar esa canción. Elegí otra:",
            reply_markup=_teclado_musica_imagen(),
        )
        estado.actualizar(estado="img_eligiendo_musica")
        return
    ruta_str = result["ruta"]  # descargar_desde_url devuelve dict {"nombre", "ruta"}
    estado.actualizar(img_musica_ruta=ruta_str)
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🎵 <b>{html.escape(cancion['titulo'][:60])}</b> lista.",
        parse_mode="HTML",
    )
    await _crear_y_enviar_reel_imagen(ctx.bot, ruta_str)


async def cb_img_buscar_cancion_volver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="img_eligiendo_musica")
    await query.edit_message_text(
        "🎵 <b>Elegí música de fondo:</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_imagen(),
    )


async def cb_img_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Reel descartado.")
    estado.reset()


async def cb_img_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


# ── /historia — Reel de historia de hincha de fútbol ─────────────────────────

async def cmd_historia(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return

    labels = list(SETTINGS.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [
            InlineKeyboardButton(label, callback_data=f"his_setting_{i + j}")
            for j, label in enumerate(labels[i:i+2])
            if i + j < len(labels)
        ]
        filas.append(fila)

    estado.actualizar(estado="his_eligiendo_setting")
    await update.message.reply_text(
        "📖 <b>Historia de Hincha</b>\n\n"
        "Claude va a escribir una historia en lunfardo para la descripción del video.\n\n"
        "¿Cuál es tu relación con el fútbol?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_his_setting(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("his_setting_"):])
    settings_list = list(SETTINGS.items())
    if idx >= len(settings_list):
        await query.edit_message_text("❌ Opción no válida.")
        return

    label, setting = settings_list[idx]
    estado.actualizar(his_setting_label=label, his_setting=setting)
    await query.edit_message_text(
        f"✅ Setting: <b>{html.escape(label)}</b>\n\n"
        "🤖 <b>Claude está escribiendo la historia...</b>\n"
        "<i>Esto tarda unos segundos.</i>",
        parse_mode="HTML",
    )
    await _generar_y_mostrar_historia(ctx.bot, setting["label_es"])


async def _generar_y_mostrar_historia(bot: Bot, label_es: str):
    loop = asyncio.get_event_loop()
    try:
        datos_ia = await loop.run_in_executor(None, generar_historia_claude, label_es)
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ Error generando historia: {e}")
        estado.reset()
        return

    estado.actualizar(his_datos=datos_ia, estado="his_aprobando_historia")

    historia  = datos_ia.get("historia", "")
    pregunta  = datos_ia.get("pregunta_video", "")
    titulo    = datos_ia.get("titulo", "")
    hashtags  = " ".join(datos_ia.get("hashtags", []))
    preview   = historia[:500] + ("..." if len(historia) > 500 else "")

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta historia", callback_data="his_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",         callback_data="his_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="his_cancelar")],
    ])
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"📝 <b>Historia generada</b>\n\n"
            f"🎯 <b>Título:</b> {html.escape(titulo)}\n"
            f"❓ <b>Pregunta video:</b> {html.escape(pregunta)}\n\n"
            f"📖 <b>Historia (preview):</b>\n"
            f"<i>{html.escape(preview)}</i>\n\n"
            f"🏷 {html.escape(hashtags)}"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_his_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos    = estado.leer()
    setting  = datos.get("his_setting", {})
    his_datos = datos.get("his_datos", {})
    terminos = setting.get("terminos", [])

    await query.edit_message_text(
        "✅ Historia aprobada.\n\n"
        "📥 <b>Descargando imágenes...</b>\n<i>30-60 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_his_")
    loop = asyncio.get_event_loop()
    try:
        imagenes = await loop.run_in_executor(None, descargar_imagenes, terminos, carpeta_tmp)
    except Exception as e:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error descargando imágenes: <code>{html.escape(str(e)[:200])}</code>\n\nUsá /cancelar y empezá de nuevo.",
            parse_mode="HTML",
        )
        estado.reset()
        return

    if len(imagenes) < 3:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ No se pudieron descargar suficientes imágenes.\n"
                "Verificá que tengas <code>PEXELS_API_KEY</code> en el <code>.env</code>."
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(his_imagenes=imagenes, his_carpeta=carpeta_tmp, estado="his_eligiendo_musica")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"✅ {len(imagenes)} imágenes listas.\n\n🎵 <b>¿Agregás música?</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_historia(),
    )


def _teclado_musica_historia() -> InlineKeyboardMarkup:
    tracks = listar_tracks()
    filas = [[InlineKeyboardButton("🔇 Sin música", callback_data="his_sin_musica")]]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"his_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"his_musica_{i+1}"))
        filas.append(fila)
    return InlineKeyboardMarkup(filas)


async def cb_his_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando historia...")
    datos    = estado.leer()
    setting  = datos.get("his_setting", {})
    await _generar_y_mostrar_historia(ctx.bot, setting.get("label_es", "un comercio"))


async def cb_his_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("his_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(his_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b> seleccionada.", parse_mode="HTML")
    await _crear_y_enviar_reel_historia(ctx.bot, track["ruta"])


async def cb_his_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música.")
    await _crear_y_enviar_reel_historia(ctx.bot, None)


async def _crear_y_enviar_reel_historia(bot: Bot, musica_ruta: str | None):
    datos     = estado.leer()
    his_datos = datos.get("his_datos", {})
    imagenes  = datos.get("his_imagenes", [])
    pregunta  = his_datos.get("pregunta_video", "")
    historia  = his_datos.get("historia", "")

    if not pregunta or not imagenes:
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /historia para empezar.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⚙️ <b>Creando el reel...</b>\n<i>~60-90 segundos.</i>",
        parse_mode="HTML",
    )

    inicio = asyncio.get_event_loop().time()

    async def _ping():
        pasos = ["⚙️ Procesando imágenes...", "🎞 Aplicando transiciones...",
                 "✍️ Agregando texto...", "🎵 Mezclando audio..."]
        i = 0
        while True:
            await asyncio.sleep(30)
            elapsed = int(asyncio.get_event_loop().time() - inicio)
            try:
                await msg.edit_text(
                    f"{pasos[min(i, len(pasos)-1)]}\n"
                    f"<i>({elapsed}s transcurridos...)</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            i += 1

    ping = asyncio.create_task(_ping())
    carpeta_out = tempfile.mkdtemp(prefix="ytbot_his_out_")
    output_path = os.path.join(carpeta_out, "historia_reel.mp4")
    loop = asyncio.get_event_loop()

    try:
        ok, err = await loop.run_in_executor(
            None, crear_reel_historia,
            imagenes, pregunta, historia, output_path, musica_ruta, 0.18,
        )
    except Exception as e:
        ping.cancel()
        await msg.edit_text(
            f"❌ Error inesperado creando el reel:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return
    ping.cancel()

    if not ok:
        await msg.edit_text(
            f"❌ Error creando el reel:\n<code>{html.escape(err[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(his_video_path=output_path, estado="his_aprobando_video")

    historia  = his_datos.get("historia", "")
    titulo    = his_datos.get("titulo", "")
    hashtags  = his_datos.get("hashtags", ["#Shorts"])
    his_label = datos.get("his_setting_label", "")

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="his_subir"),
            InlineKeyboardButton("🗑 Descartar",       callback_data="his_descartar"),
        ],
        [InlineKeyboardButton("🎙️ Agregar voz intro", callback_data="agregar_voz_intro")],
    ])

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    caption  = (
        f"📖 <b>{html.escape(titulo)}</b>\n"
        f"📍 {html.escape(his_label)}\n\n"
        f"<i>La historia completa va en la descripción de YouTube.</i>"
    )
    await msg.edit_text("✅ <b>Reel listo.</b> Enviando preview...", parse_mode="HTML")

    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID, video=f,
                    caption=caption, parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120, read_timeout=120,
                )
            return
        except Exception:
            pass

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"{caption}\n\n<i>(Video demasiado grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_his_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos      = estado.leer()
    video_path = datos.get("his_video_path", "")
    his_datos  = datos.get("his_datos", {})

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Usá /historia para empezar.")
        estado.reset()
        return

    historia    = his_datos.get("historia", "")
    pregunta    = his_datos.get("pregunta_video", "")
    titulo      = his_datos.get("titulo", "Short viral")
    hashtags    = his_datos.get("hashtags", ["#Shorts"])
    descripcion = construir_descripcion(historia, pregunta, hashtags)

    seo = {
        "titulo": titulo[:100],
        "descripcion": descripcion,
        "tags": [h.lstrip("#") for h in hashtags],
    }
    estado.actualizar(video_compilado=video_path, video_preview=video_path, seo=seo)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_his_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Reel descartado.")
    estado.reset()


async def cb_his_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


# ── /pronosticos — Pronósticos del Mundial 2026 ────────────────────────────────

async def cmd_pronosticos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text("🔮 <b>Cargando partidos de hoy...</b>", parse_mode="HTML")
    await _generar_y_mostrar_pronosticos(ctx.bot)


async def _generar_y_mostrar_pronosticos(bot: Bot):
    loop = asyncio.get_event_loop()

    try:
        partidos = await loop.run_in_executor(None, obtener_partidos_hoy)
    except Exception as e:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error consultando partidos de hoy: <code>{html.escape(str(e)[:200])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    if not partidos:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "📅 No hay partidos del Mundial 2026 <b>pendientes</b> para hoy.\n"
                "<i>(Los partidos ya terminados se excluyen del pronóstico.)</i>"
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"⚽ <b>{len(partidos)} partido(s) encontrados.</b> Generando pronósticos con IA...",
        parse_mode="HTML",
    )

    try:
        datos_ia = await loop.run_in_executor(None, generar_pronosticos_claude, partidos)
    except Exception as e:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error generando pronósticos: <code>{html.escape(str(e)[:200])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(pron_datos=datos_ia, estado="pron_aprobando")

    texto   = datos_ia.get("texto", "")
    titulo  = datos_ia.get("titulo", "")
    preview = texto[:600] + ("..." if len(texto) > 600 else "")

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Crear video", callback_data="pron_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",   callback_data="pron_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="pron_cancelar")],
    ])
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"🔮 <b>{html.escape(titulo)}</b>\n\n"
            f"<pre>{html.escape(preview)}</pre>"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_pron_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Pronósticos aprobados.\n\n🎵 <b>¿Agregás música?</b>", parse_mode="HTML")
    tracks = listar_tracks()
    filas = [[InlineKeyboardButton("🔇 Sin música", callback_data="pron_sin_musica")]]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"pron_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"pron_musica_{i+1}"))
        filas.append(fila)
    estado.actualizar(estado="pron_eligiendo_musica")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text="🎵 <b>Elegí la música:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_pron_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando pronósticos...")
    await _generar_y_mostrar_pronosticos(ctx.bot)


async def cb_pron_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("pron_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(pron_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b> seleccionada.", parse_mode="HTML")
    await _crear_y_enviar_video_pronosticos(ctx.bot, track["ruta"])


async def cb_pron_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música.")
    await _crear_y_enviar_video_pronosticos(ctx.bot, None)


async def _crear_y_enviar_video_pronosticos(bot: Bot, musica_ruta: str | None):
    datos    = estado.leer()
    pron_datos = datos.get("pron_datos", {})
    texto    = pron_datos.get("texto", "")
    titulo   = pron_datos.get("titulo", "Pronósticos IA - Mundial 2026")
    hashtags = pron_datos.get("hashtags", ["#Shorts", "#Mundial2026"])

    if not texto:
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /pronosticos para empezar.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⚙️ <b>Creando el video de pronósticos...</b>\n<i>~30 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_out = tempfile.mkdtemp(prefix="ytbot_pron_")
    output_path = os.path.join(carpeta_out, "pronosticos.mp4")
    loop = asyncio.get_event_loop()

    try:
        ok, err = await loop.run_in_executor(
            None, crear_video_pronosticos,
            pron_datos, output_path, musica_ruta, 0.18,
        )
    except Exception as e:
        await msg.edit_text(
            f"❌ Error creando video: <code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    if not ok:
        await msg.edit_text(
            f"❌ Error creando video:\n<code>{html.escape(err[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    # Aplicar campanita de suscripción
    output_ov = os.path.join(carpeta_out, "pronosticos_ov.mp4")
    try:
        ok_ov, _ = await loop.run_in_executor(None, agregar_overlay_suscripcion, output_path, output_ov)
        if ok_ov:
            output_path = output_ov
    except Exception:
        pass

    estado.actualizar(pron_video_path=output_path, estado="pron_aprobando_video")

    cta_raw  = pron_datos.get("cta", "")
    cta_desc = cta_raw.replace("\\n", " ").replace("\n", " ").strip()
    descripcion = (
        f"🤖 4 Inteligencias Artificiales predicen los partidos del Mundial 2026.\n\n"
        f"{texto}\n\n"
        f"💬 {cta_desc}\n\n"
        + "\n".join(hashtags)
    )
    seo = {
        "titulo":      titulo[:100],
        "descripcion": descripcion[:4990],
        "tags":        [h.lstrip("#") for h in hashtags],
    }
    estado.actualizar(seo=seo)

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="pron_subir"),
            InlineKeyboardButton("🗑 Descartar",        callback_data="pron_descartar"),
        ],
    ])

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    caption = f"🔮 <b>{html.escape(titulo)}</b>"

    await msg.edit_text("✅ <b>Video listo.</b> Enviando preview...", parse_mode="HTML")

    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID, video=f,
                    caption=caption, parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120, read_timeout=120,
                )
            return
        except Exception:
            pass

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"{caption}\n\n<i>(Video demasiado grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_pron_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos      = estado.leer()
    video_path = datos.get("pron_video_path", "")

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Usá /pronosticos para empezar.")
        estado.reset()
        return

    estado.actualizar(video_compilado=video_path, video_preview=video_path)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_pron_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Video descartado.")
    estado.reset()


async def cb_pron_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


# ── Auto-pronósticos (publicación automática sin intervención) ─────────────────

async def _auto_pronosticos_job(bot: Bot):
    """
    Genera y sube el reel de pronósticos completamente automático.
    Llamado por el scheduler 8 horas antes del primer partido del día.
    """
    _logger.info("Auto-pronósticos: iniciando generación...")
    loop = asyncio.get_event_loop()

    # 1. Partidos pendientes del día
    try:
        partidos = await loop.run_in_executor(None, obtener_partidos_hoy)
    except Exception as e:
        _logger.error(f"Auto-pronósticos: error ESPN — {e}")
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🔮 Auto-pronósticos: ❌ Error consultando partidos: <code>{html.escape(str(e)[:150])}</code>",
            parse_mode="HTML",
        )
        return

    if not partidos:
        _logger.info("Auto-pronósticos: sin partidos pendientes, omitiendo.")
        return

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🔮 <b>Auto-pronósticos</b> — {len(partidos)} partido(s) hoy. Generando con IA...",
        parse_mode="HTML",
    )

    # 2. Generar predicciones con Claude
    try:
        datos = await loop.run_in_executor(None, generar_pronosticos_claude, partidos)
    except Exception as e:
        _logger.error(f"Auto-pronósticos: error Claude — {e}")
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🔮 Auto-pronósticos: ❌ Error generando pronósticos: <code>{html.escape(str(e)[:150])}</code>",
            parse_mode="HTML",
        )
        return

    # 3. Música (siempre el primer track disponible)
    tracks      = listar_tracks()
    musica_ruta = tracks[0]["ruta"] if tracks else None

    # 4. Crear video
    carpeta_out = tempfile.mkdtemp(prefix="ytbot_autopron_")
    output_path = os.path.join(carpeta_out, "pronosticos_auto.mp4")
    try:
        ok, err = await loop.run_in_executor(
            None, crear_video_pronosticos, datos, output_path, musica_ruta, 0.18,
        )
    except Exception as e:
        ok, err = False, str(e)

    if not ok:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🔮 Auto-pronósticos: ❌ Error creando video: <code>{html.escape(err[:200])}</code>",
            parse_mode="HTML",
        )
        return

    # 5. Overlay de suscripción
    output_ov = os.path.join(carpeta_out, "pronosticos_auto_ov.mp4")
    try:
        ok_ov, _ = await loop.run_in_executor(
            None, agregar_overlay_suscripcion, output_path, output_ov,
        )
        if ok_ov:
            output_path = output_ov
    except Exception:
        pass

    # 6. SEO con descripción optimizada para engagement
    titulo   = datos.get("titulo", "Predicciones IA - Mundial 2026 ⚽")[:100]
    hashtags = datos.get("hashtags", ["#Shorts", "#Mundial2026"])
    texto    = datos.get("texto", "")
    cta_raw  = datos.get("cta", "")
    cta_desc = cta_raw.replace("\\n", " ").replace("\n", " ").strip()
    descripcion = (
        f"🤖 4 Inteligencias Artificiales predicen los partidos del Mundial 2026.\n\n"
        f"{texto}\n\n"
        f"💬 {cta_desc}\n\n"
        + "\n".join(hashtags)
    )
    seo = {
        "titulo":      titulo,
        "descripcion": descripcion[:4990],
        "tags":        [h.lstrip("#") for h in hashtags],
    }

    # 7. Subir a YouTube
    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🔮 <b>Auto-pronósticos</b> — Subiendo a YouTube...\n<i>{html.escape(titulo)}</i>",
        parse_mode="HTML",
    )
    try:
        url = await loop.run_in_executor(None, _subir_sync, output_path, seo, None, None)
    except Exception as e:
        _logger.error(f"Auto-pronósticos: error YouTube — {e}")
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🔮 Auto-pronósticos: ❌ Error subiendo: <code>{html.escape(str(e)[:150])}</code>",
            parse_mode="HTML",
        )
        return

    if url:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🔮 ✅ <b>Pronósticos publicados!</b>\n{html.escape(url)}",
            parse_mode="HTML",
        )
        _logger.info(f"Auto-pronósticos: publicado — {url}")
    else:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="🔮 Auto-pronósticos: ❌ No se obtuvo URL de YouTube.",
            parse_mode="HTML",
        )

    # Limpiar archivos temporales
    import shutil
    try:
        shutil.rmtree(carpeta_out, ignore_errors=True)
    except Exception:
        pass


async def verificar_y_programar_pronosticos(bot: Bot, scheduler):
    """
    Corre diariamente a las 5:00 AM Argentina.
    Busca el primer partido del día y programa la publicación 8 horas antes.
    Si ese momento ya pasó (partido temprano), publica de inmediato.
    """
    _logger.info("Verificando partidos del día para auto-pronósticos...")
    loop = asyncio.get_event_loop()

    try:
        partidos = await loop.run_in_executor(None, obtener_partidos_hoy)
    except Exception as e:
        _logger.warning(f"Check pronósticos: error obteniendo partidos — {e}")
        return

    if not partidos:
        _logger.info("Check pronósticos: sin partidos hoy.")
        return

    # Encontrar la hora del primer partido del día
    now = datetime.now(_TZ_ARG)
    primer_dt: datetime | None = None
    for p in partidos:
        hora_str = p.get("hora", "")
        if not hora_str:
            continue
        try:
            h, m    = map(int, hora_str.split(":"))
            dt      = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if primer_dt is None or dt < primer_dt:
                primer_dt = dt
        except Exception:
            continue

    if primer_dt is None:
        # Sin horario definido por la API → publicar ahora
        _logger.info("Auto-pronósticos: sin hora definida, publicando ahora.")
        asyncio.create_task(_auto_pronosticos_job(bot))
        return

    pub_dt = primer_dt - timedelta(hours=8)

    if pub_dt <= now:
        # El momento ideal ya pasó → publicar de inmediato
        _logger.info(f"Auto-pronósticos: pub_dt {pub_dt.strftime('%H:%M')} ya pasó, publicando ahora.")
        asyncio.create_task(_auto_pronosticos_job(bot))
    else:
        # Programar con APScheduler para más tarde (job de fecha única)
        scheduler.add_job(
            _auto_pronosticos_job,
            trigger="date",
            run_date=pub_dt,
            args=[bot],
            id="pron_diario_hoy",
            replace_existing=True,
        )
        _logger.info(f"Auto-pronósticos programados para las {pub_dt.strftime('%H:%M')} ARG.")
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🔮 <b>Pronósticos automáticos</b> programados para las "
                f"<b>{pub_dt.strftime('%H:%M')}</b> hs\n"
                f"<i>(8 hs antes del primer partido a las {primer_dt.strftime('%H:%M')} hs)</i>"
            ),
            parse_mode="HTML",
        )


# ── /eleccion — Reel "¿Cuál elegís?" ─────────────────────────────────────────

async def cmd_eleccion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return
    await _mostrar_categorias_eleccion(ctx.bot)


async def cb_formato_eleccion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔢 <b>¿Cuál elegís?</b>", parse_mode="HTML")
    await _mostrar_categorias_eleccion(ctx.bot)


async def _mostrar_categorias_eleccion(bot: Bot):
    labels = list(CATEGORIAS_ELECCION.keys())
    filas = []
    for i in range(0, len(labels), 2):
        fila = [InlineKeyboardButton(labels[i], callback_data=f"elc_cat_{i}")]
        if i + 1 < len(labels):
            fila.append(InlineKeyboardButton(labels[i + 1], callback_data=f"elc_cat_{i + 1}"))
        filas.append(fila)
    estado.actualizar(estado="elc_eligiendo_categoria")
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🔢 <b>¿Cuál elegís?</b>\n\n"
            "Claude genera una pregunta viral con 6 opciones numeradas y busca una imagen por opción.\n\n"
            "¿Qué categoría?"
        ),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(filas),
    )


async def cb_elc_categoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("elc_cat_"):])
    labels = list(CATEGORIAS_ELECCION.keys())
    if idx >= len(labels):
        await query.edit_message_text("❌ Opción no válida.")
        return
    label = labels[idx]
    estado.actualizar(elc_categoria=label)
    await query.edit_message_text(
        f"✅ Categoría: <b>{html.escape(label)}</b>\n\n"
        "🤖 <b>Claude está generando la pregunta y las 6 opciones...</b>",
        parse_mode="HTML",
    )
    await _generar_y_mostrar_eleccion(ctx.bot, label)


async def _generar_y_mostrar_eleccion(bot: Bot, categoria: str):
    loop = asyncio.get_event_loop()
    try:
        datos_ia = await loop.run_in_executor(None, generar_eleccion_claude, categoria)
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ Error generando pregunta: {e}")
        estado.reset()
        return

    estado.actualizar(elc_datos=datos_ia, estado="elc_aprobando_pregunta")
    pregunta = datos_ia.get("pregunta", "")
    titulo   = datos_ia.get("titulo", "")
    terminos = datos_ia.get("terminos", [])
    hashtags = " ".join(datos_ia.get("hashtags", []))

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta pregunta", callback_data="elc_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",          callback_data="elc_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="elc_cancelar")],
    ])
    nombres  = datos_ia.get("nombres", [])
    opciones_txt = "\n".join(
        f"  {i+1}. {nombres[i] if i < len(nombres) else t}"
        for i, t in enumerate(terminos[:6])
    )
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"❓ <b>Pregunta:</b> {html.escape(pregunta)}\n"
            f"📌 <b>Título YouTube:</b> {html.escape(titulo)}\n\n"
            f"🖼 <b>6 opciones:</b>\n<code>{html.escape(opciones_txt)}</code>\n\n"
            f"🏷 {html.escape(hashtags)}"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_elc_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando pregunta...")
    datos = estado.leer()
    await _generar_y_mostrar_eleccion(ctx.bot, datos.get("elc_categoria", "🚗 Coches"))


async def cb_elc_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos     = estado.leer()
    elc_datos = datos.get("elc_datos", {})
    terminos  = elc_datos.get("terminos", [])
    pregunta  = elc_datos.get("pregunta", "")

    await query.edit_message_text(
        f"✅ Pregunta: <b>{html.escape(pregunta)}</b>\n\n"
        "📥 <b>Descargando 6 imágenes...</b>\n"
        "<i>Esto puede tardar 30-60 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_tmp = tempfile.mkdtemp(prefix="ytbot_elc_")
    loop = asyncio.get_event_loop()
    try:
        imagenes = await loop.run_in_executor(None, descargar_imagenes, terminos, carpeta_tmp)
    except Exception as e:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error descargando imágenes: <code>{html.escape(str(e)[:200])}</code>\n\nUsá /cancelar y empezá de nuevo.",
            parse_mode="HTML",
        )
        estado.reset()
        return

    if len(imagenes) < 3:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ No se pudieron descargar suficientes imágenes.\n"
                "Verificá que tengas <code>PEXELS_API_KEY</code> o "
                "<code>PIXABAY_API_KEY</code> en el <code>.env</code>."
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(elc_imagenes=imagenes, elc_carpeta=carpeta_tmp, estado="elc_eligiendo_musica")
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"✅ {len(imagenes)} imágenes descargadas.\n\n🎵 <b>¿Agregás música de fondo?</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_eleccion(),
    )


def _teclado_musica_eleccion() -> InlineKeyboardMarkup:
    tracks = listar_tracks()
    filas = [
        [InlineKeyboardButton("🔍 Buscar canción en YouTube", callback_data="elc_buscar_cancion")],
        [InlineKeyboardButton("🔇 Sin música", callback_data="elc_sin_musica")],
    ]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"elc_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"elc_musica_{i+1}"))
        filas.append(fila)
    return InlineKeyboardMarkup(filas)


async def cb_elc_buscar_cancion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="elc_esperando_cancion")
    await query.edit_message_text(
        "🔍 <b>¿Qué canción querés buscar?</b>\n"
        "Escribí el nombre o el artista:",
        parse_mode="HTML",
    )


async def _mostrar_cancion_eleccion(bot: Bot, idx: int):
    datos = estado.leer()
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No hay más resultados.",
            reply_markup=_teclado_musica_eleccion(),
        )
        estado.actualizar(estado="elc_eligiendo_musica")
        return

    cancion = results[idx]
    titulo = cancion["titulo"]
    estado.actualizar(cancion_idx=idx)

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=f"⏳ <b>Descargando preview {idx + 1}/{len(results)}...</b>",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    preview_path = await loop.run_in_executor(None, descargar_preview, cancion["url"], 25)

    if preview_path:
        try:
            with open(preview_path, "rb") as f:
                await bot.send_audio(
                    chat_id=CHAT_ID, audio=f,
                    title=titulo[:64],
                    performer=f"Preview 25s · resultado {idx+1}/{len(results)}",
                    duration=25,
                )
            Path(preview_path).unlink(missing_ok=True)
        except Exception:
            pass

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta", callback_data="elc_cancion_usar"),
            InlineKeyboardButton("⏭ Siguiente", callback_data="elc_cancion_siguiente"),
        ],
        [InlineKeyboardButton("❌ Cancelar búsqueda", callback_data="elc_buscar_cancion_volver")],
    ])
    await msg.edit_text(
        f"🎵 <b>{html.escape(titulo[:80])}</b>\n"
        f"<i>Resultado {idx+1}/{len(results)}</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_elc_cancion_siguiente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0) + 1
    await query.edit_message_text("⏭ Cargando siguiente...")
    await _mostrar_cancion_eleccion(ctx.bot, idx)


async def cb_elc_cancion_usar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await query.edit_message_text("❌ Error. Volvé a elegir música.")
        estado.actualizar(estado="elc_eligiendo_musica")
        return

    cancion = results[idx]
    await query.edit_message_text(
        f"⏳ <b>Descargando:</b> {html.escape(cancion['titulo'][:60])}...",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, descargar_desde_url, cancion["url"])
    if not result:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No se pudo descargar esa canción. Elegí otra:",
            reply_markup=_teclado_musica_eleccion(),
        )
        estado.actualizar(estado="elc_eligiendo_musica")
        return
    ruta_str = result["ruta"]
    estado.actualizar(elc_musica_ruta=ruta_str)
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🎵 <b>{html.escape(cancion['titulo'][:60])}</b> lista.",
        parse_mode="HTML",
    )
    await _crear_y_enviar_reel_eleccion(ctx.bot, ruta_str)


async def cb_elc_buscar_cancion_volver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="elc_eligiendo_musica")
    await query.edit_message_text(
        "🎵 <b>Elegí música de fondo:</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_eleccion(),
    )


async def cb_elc_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("elc_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(elc_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b> seleccionada.", parse_mode="HTML")
    await _crear_y_enviar_reel_eleccion(ctx.bot, track["ruta"])


async def cb_elc_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música de fondo.")
    await _crear_y_enviar_reel_eleccion(ctx.bot, None)


async def _crear_y_enviar_reel_eleccion(bot: Bot, musica_ruta: str | None):
    datos     = estado.leer()
    elc_datos = datos.get("elc_datos", {})
    imagenes  = datos.get("elc_imagenes", [])
    pregunta  = elc_datos.get("pregunta", "")

    if not pregunta or not imagenes:
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /cancelar y empezá de nuevo.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text="⚙️ <b>Creando el reel...</b>\n<i>Numerando imágenes y componiendo, ~60-90 segundos.</i>",
        parse_mode="HTML",
    )

    inicio = asyncio.get_event_loop().time()

    async def _ping():
        pasos = ["⚙️ Numerando imágenes...", "🎞 Aplicando transiciones...",
                 "✍️ Agregando pregunta...", "🎵 Mezclando audio..."]
        i = 0
        while True:
            await asyncio.sleep(30)
            elapsed = int(asyncio.get_event_loop().time() - inicio)
            try:
                await msg.edit_text(
                    f"{pasos[min(i, len(pasos)-1)]}\n"
                    f"<i>({elapsed}s transcurridos...)</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            i += 1

    ping = asyncio.create_task(_ping())
    carpeta_out = tempfile.mkdtemp(prefix="ytbot_elc_out_")
    output_path = os.path.join(carpeta_out, "eleccion_reel.mp4")
    loop = asyncio.get_event_loop()

    nombres = datos.get("elc_datos", {}).get("nombres", [])
    try:
        ok, err = await loop.run_in_executor(
            None, crear_reel_eleccion,
            imagenes, pregunta, output_path, nombres, musica_ruta, 0.18,
        )
    except Exception as e:
        ping.cancel()
        await msg.edit_text(
            f"❌ Error inesperado creando el reel:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return
    ping.cancel()

    if not ok:
        await msg.edit_text(
            f"❌ Error creando el reel:\n<code>{html.escape(err[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    # Guardar nombres usados para no repetirlos la próxima vez
    categoria = datos.get("elc_categoria", "")
    if categoria and nombres:
        guardar_en_historial(categoria, nombres)

    estado.actualizar(elc_video_path=output_path, estado="elc_aprobando_video")

    titulo   = elc_datos.get("titulo", "")
    hashtags = " ".join(elc_datos.get("hashtags", ["#Shorts"]))

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="elc_subir"),
            InlineKeyboardButton("🗑 Descartar",       callback_data="elc_descartar"),
        ],
        [InlineKeyboardButton("🎙️ Agregar voz intro", callback_data="agregar_voz_intro")],
    ])

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    caption = f"🔢 <b>{html.escape(pregunta)}</b>\n{html.escape(hashtags)}"
    await msg.edit_text("✅ <b>Reel listo.</b> Enviando preview...", parse_mode="HTML")

    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120,
                    read_timeout=120,
                )
            return
        except Exception:
            pass

    await bot.send_message(
        chat_id=CHAT_ID,
        text=f"{caption}\n\n<i>(Video muy grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_elc_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos      = estado.leer()
    video_path = datos.get("elc_video_path", "")
    elc_datos  = datos.get("elc_datos", {})

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Usá /cancelar para empezar de nuevo.")
        estado.reset()
        return

    hashtags = " ".join(elc_datos.get("hashtags", ["#Shorts"]))
    pregunta = elc_datos.get("pregunta", "Short viral")
    titulo   = elc_datos.get("titulo") or pregunta[:60]
    seo = {
        "titulo": titulo[:100],
        "descripcion": f"{pregunta}\n\n{hashtags}",
        "tags": [h.lstrip("#") for h in elc_datos.get("hashtags", [])],
    }
    estado.actualizar(video_compilado=video_path, video_preview=video_path, seo=seo)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_elc_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Reel descartado.")
    estado.reset()


async def cb_elc_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


# ── Voz intro ElevenLabs ───────────────────────────────────────────────────────

# (video_path_key, upload_cb, discard_label, discard_cb)
_VOZ_INTRO_MAP = {
    "video_compilado": ("video_compilado", "aprobar_seo",   "🔄 Recompilar",  "recompilar"),
    "txt_video_path":  ("txt_video_path",  "txt_subir",     "🗑 Descartar",   "txt_descartar"),
    "img_video_path":  ("img_video_path",  "img_subir",     "🗑 Descartar",   "img_descartar"),
    "his_video_path":  ("his_video_path",  "his_subir",     "🗑 Descartar",   "his_descartar"),
    "elc_video_path":  ("elc_video_path",  "elc_subir",     "🗑 Descartar",   "elc_descartar"),
}


async def cb_agregar_voz_intro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    datos = estado.leer()

    # Detect which format is active
    video_key = upload_cb = discard_label = discard_cb = None
    for vk, (vk2, ub, dl, dc) in _VOZ_INTRO_MAP.items():
        if datos.get(vk2):
            video_key, upload_cb, discard_label, discard_cb = vk2, ub, dl, dc
            break

    async def _notify(text: str, reply_markup=None):
        """Edita el mensaje si es texto, sino envía uno nuevo (evita BadRequest en mensajes de video)."""
        try:
            if reply_markup:
                await query.edit_message_text(text, reply_markup=reply_markup)
            else:
                await query.edit_message_text(text)
        except Exception:
            await ctx.bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=reply_markup)

    if not video_key:
        await _notify("❌ No se encontró el video. Generá uno primero.")
        return

    video_path = datos[video_key]
    if not Path(video_path).exists():
        await _notify("❌ El video no está en disco. Generá uno primero.")
        return

    # Teclado de fallback para re-enviar si algo falla
    teclado_fallback = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Subir a YouTube", callback_data=upload_cb),
        InlineKeyboardButton(discard_label,         callback_data=discard_cb),
    ]])

    await _notify("🎙️ Generando voz intro con ElevenLabs...")

    audio_intro = await asyncio.get_event_loop().run_in_executor(None, generar_audio_intro, None)
    if not audio_intro:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No se pudo generar la voz intro. ¿Está configurado ELEVENLABS_API_KEY?\n\nPodés subir el video sin intro:",
            reply_markup=teclado_fallback,
        )
        return

    await ctx.bot.send_message(chat_id=CHAT_ID, text="🎬 Mezclando voz al video...")

    output_path = tempfile.mktemp(suffix="_intro.mp4")
    ok, err = await asyncio.get_event_loop().run_in_executor(
        None, agregar_intro_voz, video_path, audio_intro, output_path
    )
    Path(audio_intro).unlink(missing_ok=True)

    if not ok:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error al mezclar la voz:\n<code>{html.escape(err[:300])}</code>\n\nPodés subir el video sin intro:",
            parse_mode="HTML",
            reply_markup=teclado_fallback,
        )
        return

    estado.actualizar(**{video_key: output_path})

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Subir a YouTube", callback_data=upload_cb),
        InlineKeyboardButton(discard_label,         callback_data=discard_cb),
    ]])

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await ctx.bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption="✅ <b>Video con voz intro listo.</b>",
                    parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120,
                    read_timeout=120,
                )
            return
        except Exception:
            pass

    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text="✅ <b>Video con voz intro listo.</b> <i>(muy grande para preview)</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


# ── /narracion — Reel faceless con narración + stock video (Pexels) ──────────

def _teclado_temas_narracion() -> InlineKeyboardMarkup:
    temas = listar_temas_narracion()
    filas = []
    for i in range(0, len(temas), 2):
        fila = [InlineKeyboardButton(temas[i]["label"], callback_data=f"nar_tema_{i}")]
        if i + 1 < len(temas):
            fila.append(InlineKeyboardButton(temas[i + 1]["label"], callback_data=f"nar_tema_{i + 1}"))
        filas.append(fila)
    filas.append([
        InlineKeyboardButton("🎲 Sorprendeme", callback_data="nar_tema_random"),
        InlineKeyboardButton("❌ Cancelar",    callback_data="nar_cancelar"),
    ])
    return InlineKeyboardMarkup(filas)


def _teclado_musica_narracion() -> InlineKeyboardMarkup:
    tracks = listar_tracks()
    filas = [
        [InlineKeyboardButton("🔍 Buscar canción en YouTube", callback_data="nar_buscar_cancion")],
        [InlineKeyboardButton("🔇 Sin música (solo voz)",     callback_data="nar_sin_musica")],
    ]
    for i in range(0, len(tracks), 2):
        fila = [InlineKeyboardButton(f"🎵 {tracks[i]['nombre']}", callback_data=f"nar_musica_{i}")]
        if i + 1 < len(tracks):
            fila.append(InlineKeyboardButton(f"🎵 {tracks[i+1]['nombre']}", callback_data=f"nar_musica_{i+1}"))
        filas.append(fila)
    return InlineKeyboardMarkup(filas)


async def _mostrar_menu_temas_narracion(bot: Bot):
    estado.actualizar(estado="nar_eligiendo_tema")
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "🎙 <b>Reel con narración faceless</b>\n\n"
            "Claude escribe un guion gracioso, ElevenLabs le pone voz y se buscan "
            "videos stock en Pexels. La voz manda — el video se ajusta a su duración.\n\n"
            "¿Sobre qué tema?"
        ),
        parse_mode="HTML",
        reply_markup=_teclado_temas_narracion(),
    )


async def cmd_narracion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    datos = estado.leer()
    if datos["estado"] != "idle":
        await update.message.reply_text(
            f"⚠️ Hay un proceso activo: <b>{datos['estado']}</b>\nUsá /cancelar primero.",
            parse_mode="HTML",
        )
        return
    await _mostrar_menu_temas_narracion(ctx.bot)


async def _generar_y_mostrar_guion_narracion(bot: Bot, seed: str, queries_base: list[str]):
    loop = asyncio.get_event_loop()
    try:
        datos_ia = await loop.run_in_executor(None, generar_guion_narracion, seed, queries_base)
    except Exception as e:
        await bot.send_message(chat_id=CHAT_ID, text=f"❌ Error generando guion: {e}")
        estado.reset()
        return

    estado.actualizar(nar_datos=datos_ia, estado="nar_aprobando_guion")

    guion    = datos_ia.get("guion", "")
    titulo   = datos_ia.get("titulo", "")
    hashtags = " ".join(datos_ia.get("hashtags", []))
    preview  = guion[:500] + ("..." if len(guion) > 500 else "")
    palabras = len(guion.split())

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar este guion", callback_data="nar_aprobar"),
            InlineKeyboardButton("🔄 Regenerar",       callback_data="nar_regenerar"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="nar_cancelar")],
    ])
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"📝 <b>Guion generado</b> <i>({palabras} palabras)</i>\n\n"
            f"🎯 <b>Título:</b> {html.escape(titulo)}\n\n"
            f"📖 <b>Guion:</b>\n<i>{html.escape(preview)}</i>\n\n"
            f"🏷 {html.escape(hashtags)}"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_nar_tema(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("nar_tema_"):])
    temas = listar_temas_narracion()
    if idx >= len(temas):
        await query.edit_message_text("❌ Tema no válido.")
        return
    tema = temas[idx]
    estado.actualizar(
        nar_tema_id=tema["id"],
        nar_tema_label=tema["label"],
        nar_tema_seed=tema["seed"],
        nar_pexels_queries_base=tema.get("pexels_queries", []),
    )
    await query.edit_message_text(
        f"✅ Tema: <b>{html.escape(tema['label'])}</b>\n\n"
        f"🤖 <b>Claude está escribiendo el guion...</b>",
        parse_mode="HTML",
    )
    await _generar_y_mostrar_guion_narracion(ctx.bot, tema["seed"], tema.get("pexels_queries", []))


async def cb_nar_tema_random(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import random as _random
    query = update.callback_query
    await query.answer()
    temas = listar_temas_narracion()
    if not temas:
        await query.edit_message_text("❌ No hay temas configurados.")
        estado.reset()
        return
    tema = _random.choice(temas)
    estado.actualizar(
        nar_tema_id=tema["id"],
        nar_tema_label=tema["label"],
        nar_tema_seed=tema["seed"],
        nar_pexels_queries_base=tema.get("pexels_queries", []),
    )
    await query.edit_message_text(
        f"🎲 Tema al azar: <b>{html.escape(tema['label'])}</b>\n\n"
        f"🤖 <b>Claude está escribiendo el guion...</b>",
        parse_mode="HTML",
    )
    await _generar_y_mostrar_guion_narracion(ctx.bot, tema["seed"], tema.get("pexels_queries", []))


async def cb_nar_regenerar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Regenerando guion...")
    datos = estado.leer()
    await _generar_y_mostrar_guion_narracion(
        ctx.bot,
        datos.get("nar_tema_seed", ""),
        datos.get("nar_pexels_queries_base", []),
    )


async def cb_nar_aprobar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    nar_datos = datos.get("nar_datos", {})
    guion = nar_datos.get("guion", "")
    queries = nar_datos.get("pexels_queries") or datos.get("nar_pexels_queries_base", [])

    if not guion:
        await query.edit_message_text("❌ Guion vacío. Empezá con /narracion.")
        estado.reset()
        return

    # Verificar que haya créditos en ElevenLabs antes de gastar el llamado
    loop = asyncio.get_event_loop()
    ok_creditos, mensaje_creditos = await loop.run_in_executor(
        None, hay_creditos_para_guion, len(guion.split())
    )
    if not ok_creditos:
        await query.edit_message_text(mensaje_creditos, parse_mode="HTML")
        estado.reset()
        return

    await query.edit_message_text(
        "✅ Guion aprobado.\n\n"
        "🎙 <b>Generando voz con ElevenLabs...</b>\n<i>~15-30 segundos.</i>",
        parse_mode="HTML",
    )

    audio_path = await loop.run_in_executor(None, generar_audio_narracion, guion)
    if not audio_path:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ No pude generar la voz con ElevenLabs.\n"
                "Verificá que <code>ELEVENLABS_API_KEY</code> esté en el .env y que tengas créditos."
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(nar_audio_path=audio_path)

    # ── Wikipedia: imágenes de personajes mencionados en el guion ──────────────
    personajes = nar_datos.get("personajes") or []
    imagenes_personajes: list[str] = []
    carpeta_wiki = tempfile.mkdtemp(prefix="ytbot_nar_wiki_")
    if personajes:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"📷 <b>Buscando fotos en Wikipedia de:</b> "
                f"{html.escape(', '.join(personajes[:4]))}"
            ),
            parse_mode="HTML",
        )
        try:
            resultado_wiki = await loop.run_in_executor(
                None, descargar_personajes, personajes[:4], carpeta_wiki,
            )
            imagenes_personajes = list(resultado_wiki.values())
            if imagenes_personajes:
                await ctx.bot.send_message(
                    chat_id=CHAT_ID,
                    text=f"✅ {len(imagenes_personajes)}/{len(personajes)} fotos de Wikipedia descargadas.",
                )
            else:
                await ctx.bot.send_message(
                    chat_id=CHAT_ID,
                    text="ℹ️ No encontré fotos en Wikipedia, sigo solo con stock genérico.",
                )
        except Exception as e:
            await ctx.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ Wikipedia falló: <code>{html.escape(str(e)[:150])}</code>\nSigo con stock genérico.",
                parse_mode="HTML",
            )

    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text="🎬 <b>Descargando videos stock de Pexels...</b>\n<i>30-90 segundos.</i>",
        parse_mode="HTML",
    )

    carpeta_clips = tempfile.mkdtemp(prefix="ytbot_nar_clips_")
    # Ajustamos cuántos clips de stock pedir según cuántas fotos de Wikipedia conseguimos
    n_videos_stock = max(3, 6 - len(imagenes_personajes))
    try:
        clips = await loop.run_in_executor(
            None, descargar_para_narracion, queries, carpeta_clips, n_videos_stock, 4.0,
        )
    except Exception as e:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ Error bajando videos: <code>{html.escape(str(e)[:200])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    total_visuales = len(clips) + len(imagenes_personajes)
    if total_visuales < 3:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ No se pudieron descargar suficientes visuales (videos + imágenes).\n"
                "Verificá <code>PEXELS_API_KEY</code> en el .env."
            ),
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(
        nar_clips=clips,
        nar_carpeta_clips=carpeta_clips,
        nar_imagenes_personajes=imagenes_personajes,
        nar_carpeta_wiki=carpeta_wiki,
        estado="nar_eligiendo_musica",
    )
    resumen_visuales = f"{len(clips)} stock"
    if imagenes_personajes:
        resumen_visuales = f"{len(imagenes_personajes)} fotos Wikipedia + {len(clips)} stock"
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"✅ Visuales listos: <b>{resumen_visuales}</b>.\n\n"
            f"🎵 <b>¿Música de fondo?</b>\n<i>(Volumen bajo, no tapa la voz.)</i>"
        ),
        parse_mode="HTML",
        reply_markup=_teclado_musica_narracion(),
    )


async def cb_nar_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data[len("nar_musica_"):])
    tracks = listar_tracks()
    if idx >= len(tracks):
        await query.edit_message_text("❌ Track no disponible.")
        return
    track = tracks[idx]
    estado.actualizar(nar_musica_ruta=track["ruta"])
    await query.edit_message_text(f"🎵 <b>{html.escape(track['nombre'])}</b>", parse_mode="HTML")
    await _crear_y_enviar_narracion(ctx.bot, track["ruta"])


async def cb_nar_sin_musica(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔇 Sin música (solo voz).")
    await _crear_y_enviar_narracion(ctx.bot, None)


async def cb_nar_buscar_cancion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="nar_esperando_cancion")
    await query.edit_message_text(
        "🔍 <b>¿Qué canción querés buscar?</b>\nEscribí nombre o artista:",
        parse_mode="HTML",
    )


async def _mostrar_cancion_narracion(bot: Bot, idx: int):
    datos = estado.leer()
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No hay más resultados.",
            reply_markup=_teclado_musica_narracion(),
        )
        estado.actualizar(estado="nar_eligiendo_musica")
        return

    cancion = results[idx]
    titulo  = cancion["titulo"]
    estado.actualizar(cancion_idx=idx)

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=f"⏳ <b>Descargando preview {idx + 1}/{len(results)}...</b>",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    preview_path = await loop.run_in_executor(None, descargar_preview, cancion["url"], 25)
    if preview_path:
        try:
            with open(preview_path, "rb") as f:
                await bot.send_audio(
                    chat_id=CHAT_ID, audio=f,
                    title=titulo[:64],
                    performer=f"Preview 25s · resultado {idx+1}/{len(results)}",
                    duration=25,
                )
            Path(preview_path).unlink(missing_ok=True)
        except Exception:
            pass

    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Usar esta", callback_data="nar_cancion_usar"),
            InlineKeyboardButton("⏭ Siguiente", callback_data="nar_cancion_siguiente"),
        ],
        [InlineKeyboardButton("❌ Cancelar búsqueda", callback_data="nar_buscar_cancion_volver")],
    ])
    await msg.edit_text(
        f"🎵 <b>{html.escape(titulo[:80])}</b>\n"
        f"<i>Resultado {idx+1}/{len(results)}</i>",
        parse_mode="HTML",
        reply_markup=teclado,
    )


async def cb_nar_cancion_siguiente(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0) + 1
    await query.edit_message_text("⏭ Cargando siguiente...")
    await _mostrar_cancion_narracion(ctx.bot, idx)


async def cb_nar_cancion_usar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    idx = datos.get("cancion_idx", 0)
    results = datos.get("canciones_resultados", [])
    if not results or idx >= len(results):
        await query.edit_message_text("❌ Error. Volvé a elegir música.")
        estado.actualizar(estado="nar_eligiendo_musica")
        return

    cancion = results[idx]
    await query.edit_message_text(
        f"⏳ <b>Descargando:</b> {html.escape(cancion['titulo'][:60])}...",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, descargar_desde_url, cancion["url"])
    if not result:
        await ctx.bot.send_message(
            chat_id=CHAT_ID,
            text="❌ No se pudo descargar esa canción. Elegí otra:",
            reply_markup=_teclado_musica_narracion(),
        )
        estado.actualizar(estado="nar_eligiendo_musica")
        return
    ruta_str = result["ruta"]
    estado.actualizar(nar_musica_ruta=ruta_str)
    await ctx.bot.send_message(
        chat_id=CHAT_ID,
        text=f"🎵 <b>{html.escape(cancion['titulo'][:60])}</b> lista.",
        parse_mode="HTML",
    )
    await _crear_y_enviar_narracion(ctx.bot, ruta_str)


async def cb_nar_buscar_cancion_volver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    estado.actualizar(estado="nar_eligiendo_musica")
    await query.edit_message_text(
        "🎵 <b>Elegí música:</b>",
        parse_mode="HTML",
        reply_markup=_teclado_musica_narracion(),
    )


async def _crear_y_enviar_narracion(bot: Bot, musica_ruta: str | None):
    datos                = estado.leer()
    audio_path           = datos.get("nar_audio_path")
    clips                = datos.get("nar_clips", [])
    imagenes_personajes  = datos.get("nar_imagenes_personajes", [])
    nar_datos            = datos.get("nar_datos", {})

    if not audio_path or (not clips and not imagenes_personajes):
        await bot.send_message(chat_id=CHAT_ID, text="❌ Faltan datos. Usá /narracion para empezar.")
        estado.reset()
        return

    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "⚙️ <b>Compilando video con subtítulos karaoke...</b>\n"
            "<i>~60-120 segundos (la transcripción Whisper tarda un poco la primera vez).</i>"
        ),
        parse_mode="HTML",
    )

    inicio = asyncio.get_event_loop().time()

    async def _ping():
        pasos = [
            "⚙️ Normalizando clips e imágenes...",
            "🎞 Concatenando segmentos...",
            "🎙 Sincronizando voz y música...",
            "📝 Transcribiendo con Whisper...",
            "🎨 Quemando subtítulos karaoke...",
        ]
        i = 0
        while True:
            await asyncio.sleep(20)
            elapsed = int(asyncio.get_event_loop().time() - inicio)
            try:
                await msg.edit_text(
                    f"{pasos[min(i, len(pasos)-1)]}\n<i>({elapsed}s transcurridos...)</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            i += 1

    ping = asyncio.create_task(_ping())

    carpeta_out = tempfile.mkdtemp(prefix="ytbot_nar_out_")
    output_path = os.path.join(carpeta_out, "narracion.mp4")
    loop = asyncio.get_event_loop()

    def _compilar_thread():
        return compilar_video_narracion(
            audio_path, clips, output_path,
            musica=musica_ruta,
            vol_musica=0.10,
            imagenes_personajes=imagenes_personajes,
            aplicar_subtitulos=True,
        )

    try:
        ok, err = await loop.run_in_executor(None, _compilar_thread)
    except Exception as e:
        ping.cancel()
        await msg.edit_text(
            f"❌ Error inesperado compilando:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return
    ping.cancel()

    if not ok:
        await msg.edit_text(
            f"❌ Error compilando:\n<code>{html.escape(err[:400])}</code>",
            parse_mode="HTML",
        )
        estado.reset()
        return

    estado.actualizar(nar_video_path=output_path, estado="nar_aprobando_video")

    titulo   = nar_datos.get("titulo", "")
    hashtags = " ".join(nar_datos.get("hashtags", ["#Shorts"]))
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📤 Subir a YouTube", callback_data="nar_subir"),
            InlineKeyboardButton("🗑 Descartar",       callback_data="nar_descartar"),
        ],
    ])
    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    await msg.edit_text("✅ <b>Narración lista.</b> Enviando preview...", parse_mode="HTML")

    preview_enviado = False
    if size_mb < 48:
        try:
            with open(output_path, "rb") as f:
                await bot.send_video(
                    chat_id=CHAT_ID,
                    video=f,
                    caption=f"🎙 <b>{html.escape(titulo)}</b>\n{html.escape(hashtags)}",
                    parse_mode="HTML",
                    reply_markup=teclado,
                    write_timeout=120,
                    read_timeout=120,
                )
            preview_enviado = True
        except Exception:
            pass

    if not preview_enviado:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"🎙 <b>{html.escape(titulo)}</b>\n{html.escape(hashtags)}\n\n<i>(Video muy grande para preview)</i>",
            parse_mode="HTML",
            reply_markup=teclado,
        )

    # Contador de uso ElevenLabs — para saber cuántos videos más entran este mes
    uso = await loop.run_in_executor(None, obtener_uso_elevenlabs)
    if uso:
        bar = _barra_uso(uso["porcentaje_usado"])
        if uso["videos_restantes_estimados"] >= 5:
            icono = "✅"
        elif uso["videos_restantes_estimados"] >= 2:
            icono = "⚠️"
        else:
            icono = "🚨"
        msg_uso = (
            f"{icono} <b>ElevenLabs · plan {uso['tier']}</b>\n"
            f"{bar} {uso['porcentaje_usado']}%\n"
            f"<code>{uso['chars_usados']:,}</code> / <code>{uso['chars_limite']:,}</code> caracteres\n\n"
            f"🎬 Te quedan ~<b>{uso['videos_restantes_estimados']} videos</b> este mes"
        )
        if uso["videos_restantes_estimados"] == 0:
            msg_uso += "\n\n⛔ <i>No vas a poder generar más narraciones hasta el reset del plan.</i>"
        elif uso["videos_restantes_estimados"] <= 2:
            msg_uso += "\n\n⚠️ <i>Andá con cuidado — quedan pocos.</i>"
        await bot.send_message(chat_id=CHAT_ID, text=msg_uso, parse_mode="HTML")


def _barra_uso(porcentaje: float, ancho: int = 14) -> str:
    """Renderiza una barra visual estilo [████████░░░░░░] para mostrar % usado."""
    llenas = min(ancho, int(round(porcentaje / 100 * ancho)))
    vacias = ancho - llenas
    return "█" * llenas + "░" * vacias


async def cb_nar_subir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = estado.leer()
    video_path = datos.get("nar_video_path", "")
    nar_datos  = datos.get("nar_datos", {})

    if not video_path or not Path(video_path).exists():
        await _responder_query(query, ctx.bot, "❌ Video no encontrado. Empezá con /narracion.")
        estado.reset()
        return

    seo = construir_seo_narracion(nar_datos)
    estado.actualizar(video_compilado=video_path, video_preview=video_path, seo=seo)
    await _responder_query(query, ctx.bot, "📅 Calculando horario óptimo...")
    await _recomendar_horario(ctx.bot)


async def cb_nar_descartar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _responder_query(query, ctx.bot, "🗑 Narración descartada.")
    estado.reset()


async def cb_nar_cancelar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelado.")
    estado.reset()


async def cmd_uso(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Muestra el uso actual de caracteres de ElevenLabs."""
    if update.effective_chat.id != CHAT_ID:
        return
    loop = asyncio.get_event_loop()
    uso = await loop.run_in_executor(None, obtener_uso_elevenlabs)
    if not uso:
        await update.message.reply_text(
            "❌ No pude consultar tu plan de ElevenLabs.\n"
            "Verificá que <code>ELEVENLABS_API_KEY</code> esté en el .env.",
            parse_mode="HTML",
        )
        return
    bar = _barra_uso(uso["porcentaje_usado"])
    if uso["videos_restantes_estimados"] >= 5:
        icono = "✅"
    elif uso["videos_restantes_estimados"] >= 2:
        icono = "⚠️"
    else:
        icono = "🚨"
    texto = (
        f"{icono} <b>ElevenLabs · plan {uso['tier']}</b>\n\n"
        f"{bar} {uso['porcentaje_usado']}%\n"
        f"📝 <code>{uso['chars_usados']:,}</code> / <code>{uso['chars_limite']:,}</code> caracteres usados\n"
        f"💰 <code>{uso['chars_restantes']:,}</code> caracteres restantes\n\n"
        f"🎬 Te quedan ~<b>{uso['videos_restantes_estimados']} videos</b> de /narracion este mes\n"
        f"<i>(estimado a ~700 chars por video)</i>"
    )
    if uso["videos_restantes_estimados"] == 0:
        texto += "\n\n⛔ <i>No vas a poder generar narraciones hasta el reset mensual.</i>"
    elif uso["videos_restantes_estimados"] <= 2:
        texto += "\n\n⚠️ <i>Quedan pocos. El reset es el primer día del mes calendario.</i>"
    await update.message.reply_text(texto, parse_mode="HTML")


# ── Construcción de la aplicación ──────────────────────────────────────────────

def crear_aplicacion() -> Application:
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .read_timeout(30)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("trabajar",  cmd_trabajar))
    app.add_handler(CommandHandler("texto",        cmd_texto))
    app.add_handler(CommandHandler("imagen",       cmd_imagen))
    app.add_handler(CommandHandler("historia",     cmd_historia))
    app.add_handler(CommandHandler("eleccion",     cmd_eleccion))
    app.add_handler(CommandHandler("pronosticos",  cmd_pronosticos))
    app.add_handler(CommandHandler("narracion",    cmd_narracion))
    app.add_handler(CommandHandler("uso",          cmd_uso))
    app.add_handler(CommandHandler("urls",      cmd_urls))

    async def cmd_saltar(upd: Update, c: ContextTypes.DEFAULT_TYPE):
        if upd.effective_chat.id != CHAT_ID:
            return
        d = estado.leer()
        if d.get("estado") == "txt_esperando_tema":
            tipo = d.get("txt_tipo", "curiosidad")
            estado.actualizar(txt_tema="")
            await upd.message.reply_text("⏭ IA elige el tema...")
            await _generar_y_mostrar_texto(c.bot, tipo, "")
        elif d.get("estado") == "img_esperando_tema":
            estado.actualizar(img_tema="")
            await upd.message.reply_text("⏭ IA elige el tema...")
            await _generar_y_mostrar_pregunta(c.bot, "")
    app.add_handler(CommandHandler("saltar", cmd_saltar))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("stats", cmd_stats))

    app.add_handler(CallbackQueryHandler(cb_formato_video,     pattern="^formato_video$"))
    app.add_handler(CallbackQueryHandler(cb_formato_reel,      pattern="^formato_reel$"))
    app.add_handler(CallbackQueryHandler(cb_formato_ambos,     pattern="^formato_ambos$"))
    app.add_handler(CallbackQueryHandler(cb_formato_texto,     pattern="^formato_texto$"))
    app.add_handler(CallbackQueryHandler(cb_formato_imagen,    pattern="^formato_imagen$"))
    app.add_handler(CallbackQueryHandler(cb_formato_historia,  pattern="^formato_historia$"))
    app.add_handler(CallbackQueryHandler(cb_formato_eleccion,  pattern="^formato_eleccion$"))
    app.add_handler(CallbackQueryHandler(cb_musica_elegida,   pattern=r"^musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_sin_musica,       pattern="^sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_musica_sugerida,  pattern="^musica_sugerida$"))
    app.add_handler(CallbackQueryHandler(cb_buscar_cancion,   pattern="^buscar_cancion$"))
    app.add_handler(CallbackQueryHandler(cb_cancion_siguiente, pattern="^cancion_siguiente$"))
    app.add_handler(CallbackQueryHandler(cb_cancion_usar,      pattern="^cancion_usar$"))
    app.add_handler(CallbackQueryHandler(cb_audio_mantener,    pattern="^audio_mantener$"))
    app.add_handler(CallbackQueryHandler(cb_audio_sacar,       pattern="^audio_sacar$"))
    app.add_handler(CallbackQueryHandler(cb_volumen,           pattern=r"^vol_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_buscar_texto,   pattern="^buscar_texto$"))
    app.add_handler(CallbackQueryHandler(cb_pegar_links,    pattern="^pegar_links$"))
    app.add_handler(CallbackQueryHandler(cb_enviar_tiktok,      pattern="^enviar_tiktok$"))
    app.add_handler(CallbackQueryHandler(cb_tiktok_agregar_mas, pattern="^tiktok_agregar_mas$"))
    app.add_handler(CallbackQueryHandler(cb_tiktok_listo,       pattern="^tiktok_listo$"))
    app.add_handler(CallbackQueryHandler(cb_vs_iniciar,     pattern="^vs_iniciar$"))
    app.add_handler(CallbackQueryHandler(cb_vs_lados,       pattern=r"^vs_lados_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_categoria,         pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(cb_subcategoria,      pattern="^sub_"))
    app.add_handler(CallbackQueryHandler(cb_sub_subcategoria,  pattern="^subsub_"))
    app.add_handler(CallbackQueryHandler(cb_pais,           pattern="^pais_"))
    app.add_handler(CallbackQueryHandler(cb_filtro_tiempo,  pattern="^filtrotiempo_"))
    app.add_handler(CallbackQueryHandler(cb_aprobar_clips,  pattern="^aprobar_clips$"))
    app.add_handler(CallbackQueryHandler(cb_orden_ok,        pattern="^orden_ok$"))
    app.add_handler(CallbackQueryHandler(cb_orden_claude,    pattern="^orden_claude$"))
    app.add_handler(CallbackQueryHandler(cb_orden_manual,    pattern="^orden_manual$"))
    app.add_handler(CallbackQueryHandler(cb_orden_pick,      pattern=r"^orden_pick_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_buscar_otros,   pattern="^buscar_otros$"))
    app.add_handler(CallbackQueryHandler(cb_ampliar_filtro, pattern="^ampliarfiltro_"))
    app.add_handler(CallbackQueryHandler(cb_cambiar_clip,   pattern=r"^cambiar_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_aprobar_seo,        pattern="^aprobar_seo$"))
    app.add_handler(CallbackQueryHandler(cb_subir_ahora,        pattern="^subir_ahora$"))
    app.add_handler(CallbackQueryHandler(cb_hora_preset,        pattern=r"^hpreset_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_volver_seo,         pattern="^volver_seo$"))
    app.add_handler(CallbackQueryHandler(cb_elegir_hora,        pattern="^elegir_hora$"))
    app.add_handler(CallbackQueryHandler(cb_subir_custom_ok,    pattern="^subir_custom_ok$"))
    app.add_handler(CallbackQueryHandler(cb_recompilar,         pattern="^recompilar$"))
    app.add_handler(CallbackQueryHandler(cb_regenerar_seo,      pattern="^regenerar_seo$"))
    app.add_handler(CallbackQueryHandler(cb_agregar_voz_intro,  pattern="^agregar_voz_intro$"))

    # /texto — Text-on-Video Shorts
    app.add_handler(CallbackQueryHandler(cb_txt_tipo,      pattern="^txt_tipo_"))
    app.add_handler(CallbackQueryHandler(cb_txt_aprobar,   pattern="^txt_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_txt_regenerar, pattern="^txt_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_txt_fondo,     pattern=r"^txt_fondo_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_txt_musica,    pattern=r"^txt_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_txt_sin_musica, pattern="^txt_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_txt_subir,     pattern="^txt_subir$"))
    app.add_handler(CallbackQueryHandler(cb_txt_descartar, pattern="^txt_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_txt_cancelar,  pattern="^txt_cancelar$"))

    # /historia — Reel de historia de hincha de fútbol
    app.add_handler(CallbackQueryHandler(cb_his_setting,    pattern=r"^his_setting_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_his_aprobar,    pattern="^his_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_his_regenerar,  pattern="^his_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_his_musica,     pattern=r"^his_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_his_sin_musica, pattern="^his_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_his_subir,      pattern="^his_subir$"))
    app.add_handler(CallbackQueryHandler(cb_his_descartar,  pattern="^his_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_his_cancelar,   pattern="^his_cancelar$"))

    # /pronosticos — Pronósticos del Mundial 2026
    app.add_handler(CallbackQueryHandler(cb_pron_aprobar,    pattern="^pron_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_pron_regenerar,  pattern="^pron_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_pron_musica,     pattern=r"^pron_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_pron_sin_musica, pattern="^pron_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_pron_subir,      pattern="^pron_subir$"))
    app.add_handler(CallbackQueryHandler(cb_pron_descartar,  pattern="^pron_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_pron_cancelar,   pattern="^pron_cancelar$"))

    # /eleccion — Reel "¿Cuál elegís?"
    app.add_handler(CallbackQueryHandler(cb_elc_categoria,           pattern=r"^elc_cat_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_elc_aprobar,             pattern="^elc_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_elc_regenerar,           pattern="^elc_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_elc_buscar_cancion,      pattern="^elc_buscar_cancion$"))
    app.add_handler(CallbackQueryHandler(cb_elc_cancion_siguiente,   pattern="^elc_cancion_siguiente$"))
    app.add_handler(CallbackQueryHandler(cb_elc_cancion_usar,        pattern="^elc_cancion_usar$"))
    app.add_handler(CallbackQueryHandler(cb_elc_buscar_cancion_volver, pattern="^elc_buscar_cancion_volver$"))
    app.add_handler(CallbackQueryHandler(cb_elc_musica,              pattern=r"^elc_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_elc_sin_musica,          pattern="^elc_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_elc_subir,               pattern="^elc_subir$"))
    app.add_handler(CallbackQueryHandler(cb_elc_descartar,           pattern="^elc_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_elc_cancelar,            pattern="^elc_cancelar$"))

    # /imagen — Reel de pregunta viral con imágenes
    app.add_handler(CallbackQueryHandler(cb_img_tema,          pattern=r"^img_tema_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_img_tema_random,   pattern="^img_tema_random$"))
    app.add_handler(CallbackQueryHandler(cb_img_tema_escribir, pattern="^img_tema_escribir$"))
    app.add_handler(CallbackQueryHandler(cb_img_aprobar,       pattern="^img_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_img_regenerar,            pattern="^img_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_img_musica,               pattern=r"^img_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_img_sin_musica,           pattern="^img_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_img_buscar_cancion,       pattern="^img_buscar_cancion$"))
    app.add_handler(CallbackQueryHandler(cb_img_cancion_siguiente,    pattern="^img_cancion_siguiente$"))
    app.add_handler(CallbackQueryHandler(cb_img_cancion_usar,         pattern="^img_cancion_usar$"))
    app.add_handler(CallbackQueryHandler(cb_img_buscar_cancion_volver, pattern="^img_buscar_cancion_volver$"))
    app.add_handler(CallbackQueryHandler(cb_img_subir,                pattern="^img_subir$"))
    app.add_handler(CallbackQueryHandler(cb_img_descartar,            pattern="^img_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_img_cancelar,             pattern="^img_cancelar$"))

    # ── Callbacks de /narracion ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(cb_nar_tema,                 pattern=r"^nar_tema_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_nar_tema_random,          pattern="^nar_tema_random$"))
    app.add_handler(CallbackQueryHandler(cb_nar_aprobar,              pattern="^nar_aprobar$"))
    app.add_handler(CallbackQueryHandler(cb_nar_regenerar,            pattern="^nar_regenerar$"))
    app.add_handler(CallbackQueryHandler(cb_nar_musica,               pattern=r"^nar_musica_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_nar_sin_musica,           pattern="^nar_sin_musica$"))
    app.add_handler(CallbackQueryHandler(cb_nar_buscar_cancion,       pattern="^nar_buscar_cancion$"))
    app.add_handler(CallbackQueryHandler(cb_nar_cancion_siguiente,    pattern="^nar_cancion_siguiente$"))
    app.add_handler(CallbackQueryHandler(cb_nar_cancion_usar,         pattern="^nar_cancion_usar$"))
    app.add_handler(CallbackQueryHandler(cb_nar_buscar_cancion_volver, pattern="^nar_buscar_cancion_volver$"))
    app.add_handler(CallbackQueryHandler(cb_nar_subir,                pattern="^nar_subir$"))
    app.add_handler(CallbackQueryHandler(cb_nar_descartar,            pattern="^nar_descartar$"))
    app.add_handler(CallbackQueryHandler(cb_nar_cancelar,             pattern="^nar_cancelar$"))

    # Captura mensajes de texto en todos los estados activos
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_texto))

    return app


async def ejecutar_workflow_automatico(bot: Bot):
    """Llamado por el scheduler. Inicia el workflow si está idle."""
    datos = estado.leer()
    if datos["estado"] != "idle":
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Workflow automático cancelado — proceso activo: `{datos['estado']}`",
            parse_mode="Markdown",
        )
        return
    await bot.send_message(chat_id=CHAT_ID, text="🤖 Iniciando workflow automático...")
    await _preguntar_formato(bot)
