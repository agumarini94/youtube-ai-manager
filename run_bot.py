"""
Punto de entrada del bot de automatización.
Inicia el bot de Telegram + el scheduler automático.

Uso:
    cd youtube_ai_manager
    python run_bot.py

El bot escucha comandos y también ejecuta el workflow automáticamente
de lunes a viernes a las 18:00 (hora de Argentina).

Comandos disponibles en Telegram:
    /trabajar       — inicia el workflow manualmente
    /urls <url>...  — usa URLs de TikTok específicas
    /estado         — muestra el estado actual
    /cancelar       — cancela el proceso en curso
"""
import asyncio
import html
import logging
import os
import signal
import traceback
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import ContextTypes
from modules.telegram_bot import crear_aplicacion, ejecutar_workflow_automatico

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.INFO)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_CHAT_ID:
    raise ValueError("Falta la variable de entorno TELEGRAM_CHAT_ID en el archivo .env")
CHAT_ID = int(TELEGRAM_CHAT_ID)
HORA_AUTO = int(os.getenv("HORA_AUTO", "18"))
MINUTO_AUTO = int(os.getenv("MINUTO_AUTO", "0"))

_WATCHDOG_INTERVAL = 120   # segundos entre chequeos de conectividad


# ── Error handler ──────────────────────────────────────────────────────────────

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura errores no manejados y los reporta por Telegram."""
    err = context.error

    # Errores ignorables: queries vencidas (usuario tocó un botón viejo tras reinicio)
    ignorable = (
        "query is too old",
        "query id is invalid",
        "message is not modified",
        "message to edit not found",
    )
    if err and any(msg in str(err).lower() for msg in ignorable):
        logging.warning("Ignored stale query error: %s", err)
        return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    logging.error("Unhandled exception in handler", exc_info=err)
    # Solo las últimas 6 líneas del traceback para no saturar el chat
    resumen = "\n".join(tb.strip().splitlines()[-6:])
    try:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"⚠️ <b>Error en el bot</b>\n"
                f"<code>{html.escape(resumen)}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ── Heartbeat ──────────────────────────────────────────────────────────────────

async def _heartbeat(bot):
    """Notificación diaria de que el bot sigue vivo."""
    hora = datetime.now().strftime("%H:%M")
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=f"💓 <b>Bot activo</b> — {hora} hs · todo en orden.",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ── Watchdog de conectividad ───────────────────────────────────────────────────

async def _watchdog_conectividad(app):
    """
    Comprueba cada 2 minutos si el bot puede alcanzar Telegram.
    Si detecta desconexión y luego reconexión, avisa por Telegram.
    """
    conectado = True
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL)
        try:
            await app.bot.get_me()
            if not conectado:
                conectado = True
                try:
                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text="✅ <b>Bot reconectado</b> — la conexión se restableció y el bot está online.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
        except Exception:
            if conectado:
                conectado = False
                logging.warning("Watchdog: sin conexión a Telegram")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    app = crear_aplicacion()

    # Registrar error handler global
    app.add_error_handler(_error_handler)

    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")

    from apscheduler.events import EVENT_JOB_ERROR
    def _job_error_listener(event):
        logging.error(f"Job falló: {event.job_id} — {event.exception}")
    scheduler.add_listener(_job_error_listener, EVENT_JOB_ERROR)

    # Workflow automático diario
    scheduler.add_job(
        ejecutar_workflow_automatico,
        trigger="cron",
        day_of_week="mon-fri",
        hour=HORA_AUTO,
        minute=MINUTO_AUTO,
        args=[app.bot],
        id="workflow_diario",
        replace_existing=True,
    )

    # Heartbeat diario a las 9:00 AM
    scheduler.add_job(
        _heartbeat,
        trigger="cron",
        hour=9,
        minute=0,
        args=[app.bot],
        id="heartbeat_diario",
        replace_existing=True,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await app.initialize()

    # Registrar comandos para autocompletado al escribir "/" en Telegram
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("trabajar",  "Crear contenido (Texto, Imagen, Historia, TikTok)"),
        BotCommand("texto",     "Short de texto sobre fondo de color"),
        BotCommand("imagen",    "Reel de pregunta viral con imágenes"),
        BotCommand("historia",  "Historia de atención al público"),
        BotCommand("eleccion",  "Reel ¿Cuál elegís? con 6 opciones numeradas"),
        BotCommand("urls",      "Compilar con URLs de TikTok"),
        BotCommand("stats",     "Ver estadísticas de los últimos 10 videos"),
        BotCommand("estado",    "Ver el estado actual del bot"),
        BotCommand("cancelar",  "Cancelar el proceso en curso"),
        BotCommand("ping",      "Verificar que el bot está activo"),
    ])

    scheduler.start()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logging.info(
        f"Bot iniciado. Workflow automático: lun-vie a las {HORA_AUTO:02d}:{MINUTO_AUTO:02d} (Argentina)"
    )

    # Notificar que el bot arrancó
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🟢 <b>Bot iniciado</b> — listo para recibir comandos.\n"
                f"<i>Workflow automático: lun-vie {HORA_AUTO:02d}:{MINUTO_AUTO:02d} · "
                f"Heartbeat: todos los días 09:00</i>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Lanzar watchdog de conectividad en segundo plano
    watchdog_task = asyncio.create_task(_watchdog_conectividad(app))

    await stop_event.wait()

    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

    # Notificar apagado antes de cerrar
    logging.info("Apagando bot...")
    try:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text="🔴 <b>Bot detenido</b> — se recibió señal de apagado.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    scheduler.shutdown(wait=False)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
