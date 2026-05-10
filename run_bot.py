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
import logging
import os
import signal

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from modules.telegram_bot import crear_aplicacion, ejecutar_workflow_automatico

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.INFO)

CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
HORA_AUTO = int(os.getenv("HORA_AUTO", "18"))   # hora local (Argentina)
MINUTO_AUTO = int(os.getenv("MINUTO_AUTO", "0"))


_WATCHDOG_INTERVAL = 120   # segundos entre chequeos de conectividad


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


async def main():
    app = crear_aplicacion()

    scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires")
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

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await app.initialize()
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
            text="🟢 <b>Bot iniciado</b> — listo para recibir comandos.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Lanzar watchdog de conectividad en segundo plano
    asyncio.create_task(_watchdog_conectividad(app))

    await stop_event.wait()

    logging.info("Apagando bot...")
    scheduler.shutdown(wait=False)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
