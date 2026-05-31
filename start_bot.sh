#!/bin/bash
# start_bot.sh — lanza el bot y lo reinicia automáticamente si crashea.
# Uso: ./start_bot.sh
#      ./start_bot.sh &    (en segundo plano)

cd "$(dirname "$0")"
source venv/bin/activate

REINTENTOS=0
MAX_REINTENTOS=10   # evitar loop infinito si hay un error de configuración

while [ $REINTENTOS -lt $MAX_REINTENTOS ]; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando bot (intento $((REINTENTOS + 1)))..."
    python3 run_bot.py
    EXIT_CODE=$?

    # Salida limpia (Ctrl+C o SIGTERM) — no reiniciar
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot detenido correctamente (código $EXIT_CODE). No se reinicia."
        break
    fi

    REINTENTOS=$((REINTENTOS + 1))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bot terminó con error (código $EXIT_CODE). Reiniciando en 15s... ($REINTENTOS/$MAX_REINTENTOS)"
    sleep 15
done

if [ $REINTENTOS -ge $MAX_REINTENTOS ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Se alcanzó el límite de reinicios ($MAX_REINTENTOS). Revisá los logs."
fi
