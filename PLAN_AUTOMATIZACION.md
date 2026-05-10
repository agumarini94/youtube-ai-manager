# Plan: Automatización completa con aprobación por Telegram

## Idea general

Mientras estás en el trabajo, la IA busca videos de TikTok, los compila, genera el SEO
y te pide aprobación por Telegram antes de subir nada a YouTube. Vos solo respondés con
👍 o 👎 desde el celular. Sin tocar la PC.

---

## Flujo completo paso a paso

```
[Scheduler arranca de noche o de mañana]
        ↓
1. IA busca videos en TikTok (tikwm API, ya implementada)
   - Filtra por duración ~10 segundos
   - Filtra por vistas mínimas (calidad)
   - Evita videos ya usados antes (historial en JSON)
   - Selecciona 10-15 candidatos, elige los mejores 3-4
        ↓
2. Descarga los videos seleccionados (ya implementado)
        ↓
3. Compila en video largo (ya implementado — grupos de 4)
        ↓
4. Genera SEO: título + descripción + tags (ya implementado)
        ↓
5. Bot de Telegram te manda el video compilado
   → 👍  → pasa al paso 6
   → 👎  → te pregunta qué clip eliminar (opción 1, 2, 3...)
            rehace la compilación y vuelve al paso 5
        ↓
6. Bot de Telegram te manda el SEO (título + descripción + tags)
   → 👍  → sube el video a YouTube (ya implementado)
   → 👎  → te pregunta si querés regenerar el SEO o editarlo manualmente
            vuelve al paso 6
        ↓
7. Bot te confirma: "✅ Video subido: [título] — [link de YouTube]"
```

---

## Componentes a construir

### A) Scheduler (coordinador central)
- Archivo: `modules/scheduler.py`
- Librería: `APScheduler` (Python puro, sin servidor externo)
- Corre como proceso en background: `python scheduler.py`
- Define cuándo ejecutar el flujo (ej: todos los días a las 8am)
- Orquesta los pasos 1 al 7

### B) Selector automático de videos por IA
- Archivo: `modules/selector_ia.py`
- Usa la API de tikwm (ya usada en tiktok_feed.py) para buscar por hashtag/nicho
- Le pasa los 10-15 candidatos a Claude con metadata (duración, vistas, título)
- Claude elige los mejores 3-4 según criterios de calidad y variedad
- Consulta el historial para no repetir videos ya usados

### C) Historial de videos usados
- Archivo: `data/historial_videos.json`
- Guarda: URL, id, título, canal, fecha de uso
- Se consulta antes de cada selección para evitar repetidos
- Fácil de filtrar por fecha (últimos 7/30 días)

### D) Bot de Telegram
- Archivo: `modules/telegram_bot.py`
- Librería: `python-telegram-bot` (v20+, asyncio)
- Funciones:
  - Enviar video (como archivo o link de descarga si supera límite de tamaño)
  - Enviar texto con SEO formateado
  - Recibir 👍 / 👎 y responder según el estado del flujo
  - Preguntar qué clip eliminar con botones numerados
  - Confirmar subida exitosa

### E) Máquina de estados del flujo
- Dentro de `telegram_bot.py` o en `modules/flujo_estado.py`
- Guarda en qué paso del flujo está el proceso actual
- Estados: `ESPERANDO_APROBACION_VIDEO`, `ESPERANDO_APROBACION_SEO`, `SUBIENDO`, `COMPLETADO`
- Persiste en un JSON (`data/estado_flujo.json`) para sobrevivir reinicios

---

## Setup de Telegram (5 minutos)

1. Abrir Telegram → buscar `@BotFather`
2. Enviar `/newbot` → elegir nombre → guardar el **token**
3. Hablar con tu bot una vez para obtener tu **chat_id**
4. Agregar al `.env`:
   ```
   TELEGRAM_TOKEN=xxxx:yyyy
   TELEGRAM_CHAT_ID=123456789
   ```

---

## Dependencias nuevas a agregar en requirements.txt

```
python-telegram-bot>=20.0
APScheduler>=3.10
```

---

## Estructura de archivos nueva

```
youtube_ai_manager/
├── modules/
│   ├── scheduler.py          ← nuevo: coordina todo el flujo
│   ├── selector_ia.py        ← nuevo: selección automática con Claude
│   ├── telegram_bot.py       ← nuevo: bot de aprobación
│   └── flujo_estado.py       ← nuevo: máquina de estados
├── data/
│   ├── historial_videos.json ← nuevo: videos ya usados
│   └── estado_flujo.json     ← nuevo: estado actual del proceso
├── run_bot.py                ← nuevo: punto de entrada para arrancar el bot
└── .env                      ← agregar TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
```

---

## Orden sugerido para implementar

1. `historial_videos.json` + lógica de guardado/consulta — 30 min
2. `selector_ia.py` — 1 hora
3. `telegram_bot.py` básico (enviar/recibir mensajes) — 1 hora
4. Flujo de aprobación de video (👍/👎 + re-edición) — 1 hora
5. Flujo de aprobación de SEO (👍/👎) — 30 min
6. `scheduler.py` que une todo — 1 hora
7. Prueba end-to-end con un nicho real — 30 min

**Total estimado: ~6 horas de trabajo**

---

## Lo que ya está hecho y se reutiliza tal cual

| Componente | Archivo actual |
|---|---|
| Descarga de TikTok sin marca de agua | `modules/tiktok_feed.py` |
| Compilación en grupos de 4 | `modules/tiktok_feed.py` |
| Generación de SEO con Claude | `modules/seo_gen.py` |
| Subida a YouTube | `modules/uploader.py` |
| Autenticación OAuth YouTube | `token_youtube.json` |

---

## Requisito de hardware: la Mac debe estar encendida

El bot y el scheduler corren en tu Mac, por lo tanto:

- ✅ Mac encendida + conectada a corriente = todo funciona
- ❌ Mac apagada o en sleep profundo = el bot no corre

**Solución para que se despierte sola:**
Ir a Ajustes del sistema → Batería → Programar, y configurar que la Mac
se encienda automáticamente a las 18:00. Así no tenés que acordarte.
También se puede programar que se apague sola a las 20:00 una vez que el
bot terminó de trabajar.

---

## Horario de trabajo y control manual

### Opción 1 — Horario fijo automático (recomendado)
Configurar el scheduler para que corra de lunes a viernes de 18:00 a 20:00.
La Mac se despierta sola, el bot trabaja, te manda el video por Telegram.
Vos respondés desde el celular cuando querés, sin apuro.

```python
# Ejemplo en scheduler.py
scheduler.add_job(
    ejecutar_flujo_completo,
    trigger="cron",
    day_of_week="mon-fri",
    hour=18,
    minute=0,
)
```

### Opción 2 — Activación manual por Telegram
Le mandás un comando al bot desde el celular y él arranca:

| Comando | Acción |
|---|---|
| `/trabajar` | Inicia el flujo completo ahora mismo |
| `/parar` | Detiene el proceso actual |
| `/estado` | Te dice en qué paso está y qué está haciendo |

### Opción combinada (lo más cómodo)
Horario automático de lunes a viernes 18:00–20:00, pero con la opción
de mandarlo manualmente cualquier día si querés un video extra o si
el horario de ese día no te cierra.

---

## Notas importantes

- El bot de Telegram y el scheduler corren **separados de Streamlit** — son procesos Python independientes
- Telegram tiene límite de 50MB para enviar videos directamente; si el video pesa más, se manda un link de descarga local (requiere que la Mac esté accesible, o se sube temporalmente a un servicio gratuito como file.io)
- Todo corre en tu Mac, no necesitás servidor externo ni pagar nada adicional
