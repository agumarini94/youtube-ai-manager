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
import os
import tempfile
from pathlib import Path

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
from modules.selector_ia import (
    CATEGORIAS,
    buscar_hashtags,
    filtrar_por_seguidores,
    seleccionar_con_claude,
    info_desde_urls,
)
from modules.workflow import compilar_y_generar_seo, subir_a_youtube

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

_MAX_DUR = {"video": 60, "reel": 15, "ambos": 15}
_N_CLIPS = {"video": 4, "reel": 6, "ambos": 6}   # reels/ambos usan más clips cortos

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
}


# ── Helpers ────────────────────────────────────────────────────────────────────

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
    texto_seo = (
        f"📋 <b>SEO GENERADO</b>\n\n"
        f"📌 <b>Título:</b>\n{html.escape(titulo)}\n\n"
        f"📝 <b>Descripción:</b>\n{html.escape(desc_preview)}\n\n"
        f"🏷 <b>Tags:</b> {html.escape(tags_txt)}"
        f"{nota_dual}"
    )
    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Subir a YouTube", callback_data="aprobar_seo"),
        InlineKeyboardButton("🔄 Recompilar", callback_data="recompilar"),
    ], [
        InlineKeyboardButton("✏️ Regenerar SEO", callback_data="regenerar_seo"),
    ]])
    await bot.send_message(
        chat_id=CHAT_ID, text=texto_seo, parse_mode="HTML", reply_markup=teclado
    )


# ── Funciones bloqueantes (se corren en executor) ──────────────────────────────

def _compilar_sync(clips: list[dict], formato: str, progress_cb=None) -> dict:
    return compilar_y_generar_seo(clips, formato=formato, progress_cb=progress_cb)


def _subir_sync(video: str, seo: dict, fecha_publicacion: str | None = None, thumbnail: str | None = None) -> str | None:
    return subir_a_youtube(video, seo, fecha_publicacion=fecha_publicacion, thumbnail=thumbnail)


# ── Pasos del flujo ────────────────────────────────────────────────────────────

async def _preguntar_formato(bot: Bot):
    teclado = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 YouTube Video", callback_data="formato_video"),
            InlineKeyboardButton("🎬 Short / Reel",  callback_data="formato_reel"),
        ],
        [
            InlineKeyboardButton("📺+🎬 Ambos formatos", callback_data="formato_ambos"),
        ],
    ])
    await bot.send_message(
        chat_id=CHAT_ID,
        text=(
            "¿Qué tipo de contenido querés crear?\n\n"
            "<i>📺+🎬 Ambos: compila un Short y un Video horizontal con los mismos clips "
            "— máxima distribución en YouTube.</i>"
        ),
        parse_mode="HTML",
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_formato")


async def _preguntar_categoria(bot: Bot):
    """Muestra las categorías de búsqueda como botones inline (2 columnas)."""
    labels = list(CATEGORIAS.keys())
    filas = [
        [
            InlineKeyboardButton("✍️ Buscar por texto", callback_data="buscar_texto"),
            InlineKeyboardButton("🔗 Pegar links",      callback_data="pegar_links"),
        ]
    ]
    for i in range(0, len(labels), 2):
        fila = []
        for label in labels[i:i+2]:
            tag = CATEGORIAS[label]
            fila.append(InlineKeyboardButton(label, callback_data=f"cat_{tag}"))
        filas.append(fila)
    teclado = InlineKeyboardMarkup(filas)
    await bot.send_message(
        chat_id=CHAT_ID,
        text="¿Qué tipo de videos querés buscar?",
        reply_markup=teclado,
    )
    estado.actualizar(estado="eligiendo_categoria")


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


async def _iniciar_busqueda(bot: Bot):
    datos = estado.leer()
    formato = datos.get("formato", "video")
    tag = datos.get("tag_busqueda")   # puede ser None si no se eligió categoría
    pais = datos.get("pais_busqueda")  # None = global
    n = _N_CLIPS[formato]
    max_dur = _MAX_DUR[formato]
    loop = asyncio.get_event_loop()
    estado.actualizar(estado="buscando")

    # Paso 1: buscar en hashtags
    etiqueta_cat = next((k for k, v in CATEGORIAS.items() if v == tag), tag or "tendencias generales")
    etiqueta_pais = next((k for k, v in PAISES.items() if v == pais), "") if pais else ""
    etiqueta_display = f"{etiqueta_cat}" + (f" · {etiqueta_pais}" if etiqueta_pais else "")
    msg = await bot.send_message(
        chat_id=CHAT_ID,
        text=f"🔍 <b>Paso 1/3</b> — Buscando en TikTok: <i>{html.escape(etiqueta_display)}</i>...",
        parse_mode="HTML",
    )
    candidatos = await loop.run_in_executor(None, buscar_hashtags, max_dur, tag, pais)

    if not candidatos:
        estado.reset()
        await msg.edit_text(
            "❌ No encontré videos con esos filtros.\n\n"
            "Podés pasarme URLs directamente:\n"
            "/urls https://tiktok.com/... https://tiktok.com/..."
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
        estado.reset()
        await msg.edit_text(
            "❌ Todos los videos encontrados son de cuentas con más de 2M seguidores.\n"
            "Intentá de nuevo con /trabajar."
        )
        return

    # Paso 3: Claude elige los mejores
    await msg.edit_text(
        f"🤖 <b>Paso 3/3</b> — Claude eligiendo los mejores {n} clips de {len(candidatos)} candidatos...",
        parse_mode="HTML",
    )
    clips = await loop.run_in_executor(None, seleccionar_con_claude, candidatos, n)

    if not clips:
        estado.reset()
        await msg.edit_text("❌ No pude seleccionar clips. Intentá de nuevo.")
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

    resultado = await loop.run_in_executor(
        None, _compilar_sync, clips, formato, progress_cb
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
            model="claude-3-haiku-20240307",
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

    if fecha_publicacion:
        await bot.send_message(chat_id=CHAT_ID, text="📤 Subiendo a YouTube como privado y programando publicación...")
    else:
        await bot.send_message(chat_id=CHAT_ID, text="📤 Subiendo a YouTube... (puede tardar ~2 min)")
    estado.actualizar(estado="subiendo")

    loop = asyncio.get_event_loop()
    url = await loop.run_in_executor(None, _subir_sync, video, seo, fecha_publicacion, thumbnail)

    # Si hay versión horizontal, subirla también
    url_horizontal = None
    if tiene_horizontal and url:
        await bot.send_message(chat_id=CHAT_ID, text="📺 Subiendo versión horizontal (YouTube Video)...")
        url_horizontal = await loop.run_in_executor(None, _subir_sync, video_horizontal, seo, fecha_publicacion, thumbnail)

    estado.reset()
    if url:
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
    else:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "❌ Error al subir a YouTube.\n"
                "Verificá que el token esté activo "
                "(Streamlit → Subida a YouTube → autenticar)."
            ),
        )


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


async def cb_categoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler genérico para cualquier botón de categoría (pattern: ^cat_)."""
    query = update.callback_query
    await query.answer()
    tag = query.data[4:]   # quita el prefijo "cat_"
    label = next((k for k, v in CATEGORIAS.items() if v == tag), tag)
    await query.edit_message_text(f"🎯 Categoría elegida: <b>{html.escape(label)}</b>", parse_mode="HTML")
    estado.actualizar(tag_busqueda=tag)
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
    await _iniciar_busqueda(ctx.bot)


async def cb_aprobar_clips(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Clips aprobados. Iniciando compilación...")
    await _iniciar_compilacion(ctx.bot)


async def cb_buscar_otros(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔄 Cambiando categoría...")
    estado.actualizar(estado="idle")
    await _preguntar_categoria(ctx.bot)


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
    await query.edit_message_text("✅ SEO aprobado. Calculando horario óptimo...")
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

    if est == "esperando_keyword":
        keyword = texto.lower().replace(" ", "")
        await update.message.reply_text(
            f"✍️ Tema: <b>{html.escape(texto)}</b>",
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
    await query.edit_message_text("🔄 Buscando clips nuevos...")
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


# ── Construcción de la aplicación ──────────────────────────────────────────────

def crear_aplicacion() -> Application:
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("trabajar", cmd_trabajar))
    app.add_handler(CommandHandler("urls", cmd_urls))
    app.add_handler(CommandHandler("estado", cmd_estado))
    app.add_handler(CommandHandler("cancelar", cmd_cancelar))
    app.add_handler(CommandHandler("ping", cmd_ping))

    app.add_handler(CallbackQueryHandler(cb_formato_video,  pattern="^formato_video$"))
    app.add_handler(CallbackQueryHandler(cb_formato_reel,   pattern="^formato_reel$"))
    app.add_handler(CallbackQueryHandler(cb_formato_ambos,  pattern="^formato_ambos$"))
    app.add_handler(CallbackQueryHandler(cb_buscar_texto,   pattern="^buscar_texto$"))
    app.add_handler(CallbackQueryHandler(cb_pegar_links,    pattern="^pegar_links$"))
    app.add_handler(CallbackQueryHandler(cb_categoria,      pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(cb_pais,           pattern="^pais_"))
    app.add_handler(CallbackQueryHandler(cb_aprobar_clips,  pattern="^aprobar_clips$"))
    app.add_handler(CallbackQueryHandler(cb_buscar_otros,   pattern="^buscar_otros$"))
    app.add_handler(CallbackQueryHandler(cb_cambiar_clip,   pattern=r"^cambiar_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_aprobar_seo,        pattern="^aprobar_seo$"))
    app.add_handler(CallbackQueryHandler(cb_subir_ahora,        pattern="^subir_ahora$"))
    app.add_handler(CallbackQueryHandler(cb_hora_preset,        pattern=r"^hpreset_\d+$"))
    app.add_handler(CallbackQueryHandler(cb_volver_seo,         pattern="^volver_seo$"))
    app.add_handler(CallbackQueryHandler(cb_elegir_hora,        pattern="^elegir_hora$"))
    app.add_handler(CallbackQueryHandler(cb_subir_custom_ok,    pattern="^subir_custom_ok$"))
    app.add_handler(CallbackQueryHandler(cb_recompilar,         pattern="^recompilar$"))
    app.add_handler(CallbackQueryHandler(cb_regenerar_seo,      pattern="^regenerar_seo$"))

    # Captura mensajes de texto en estados: esperando_hora_custom, esperando_keyword, esperando_urls
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
