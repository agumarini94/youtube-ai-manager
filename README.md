# YouTube AI Manager

Automatizá tu canal de YouTube con inteligencia artificial. Buscá clips virales de TikTok, compilalos, generá SEO optimizado, thumbnail con texto y subí a YouTube — todo desde Telegram o desde una interfaz web en Streamlit.

---

## ¿Qué hace?

- **Bot de Telegram** — workflow completo desde el celular: buscar clips → aprobar → compilar → publicar
- **Búsqueda inteligente** — por categoría, por texto libre o pegando links directamente
- **Filtro por país** — Argentina, México, Brasil, Colombia, Chile, España, EEUU o global
- **Compilación automática** — descarga y une los clips con FFmpeg
- **SEO con Claude** — título, descripción y tags optimizados para YouTube, con análisis visual del video
- **Thumbnail automático** — extrae el mejor frame y superpone texto generado por IA
- **Horario inteligente** — publica en el horario pico de tu audiencia (hoy si es buen día, sino el próximo)
- **Link al video anterior** — cada descripción incluye automáticamente el link al video anterior del canal
- **Interfaz web** — Streamlit para configurar, ver historial, editar SEO y subir manualmente

---

## Requisitos previos

- Python 3.10 o superior
- FFmpeg instalado (`brew install ffmpeg` en Mac / `sudo apt install ffmpeg` en Linux)
- Cuenta en [Anthropic](https://console.anthropic.com) (Claude)
- Canal de YouTube con Google Cloud configurado
- Bot de Telegram (opcional, para el workflow automático)
- Cuenta en [ElevenLabs](https://elevenlabs.io) (opcional, para voz)

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/youtube-ai-manager.git
cd youtube-ai-manager

# 2. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Abrir .env y completar con tus keys reales

# 5. Iniciar la app web
streamlit run app.py

# 5b. O iniciar el bot de Telegram (en otra terminal)
python3 run_bot.py
```

---

## Variables de entorno

Copiá `.env.example` como `.env` y completá cada valor:

| Variable | Descripción |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API — [console.anthropic.com](https://console.anthropic.com) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 — Google Cloud Console |
| `YOUTUBE_CLIENT_SECRETS_FILE` | Ruta al `client_secret.json` (default: `client_secret.json`) |
| `YOUTUBE_CHANNEL_ID` | ID de tu canal (YouTube Studio → Configuración → Info del canal) |
| `YOUTUBE_TARGET_CHANNEL` | Nombre de tu canal (ej: `ViralLocos`) |
| `ELEVENLABS_API_KEY` | ElevenLabs — opcional, para generador de voz |
| `ELEVENLABS_VOICE_ID` | ID de voz ElevenLabs — opcional |
| `TELEGRAM_TOKEN` | Token del bot — [t.me/BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Tu Chat ID — [@userinfobot](https://t.me/userinfobot) |
| `HORA_AUTO` | Hora del workflow automático diario (default: `18`) |
| `MINUTO_AUTO` | Minuto del workflow automático (default: `0`) |

---

## Configurar YouTube (OAuth2)

Para poder subir videos necesitás crear credenciales OAuth2 en Google Cloud:

1. Ir a [console.cloud.google.com](https://console.cloud.google.com) y crear un proyecto
2. Habilitar **YouTube Data API v3**
3. Ir a **Credenciales → Crear credenciales → ID de cliente OAuth**
4. Tipo: **Aplicación de escritorio**
5. Descargar el JSON y renombrarlo `client_secret.json` en la raíz del proyecto
6. En **Pantalla de consentimiento**, agregar tu email como usuario de prueba
7. En la app web (Streamlit → Subida a YouTube), completar el flujo de autenticación

---

## Configurar el bot de Telegram

1. Hablar con [@BotFather](https://t.me/BotFather) → `/newbot` → copiar el token
2. Hablar con [@userinfobot](https://t.me/userinfobot) → copiar tu Chat ID
3. Pegar ambos valores en `.env`
4. Ejecutar `python3 run_bot.py`

### Comandos disponibles

| Comando | Descripción |
|---|---|
| `/trabajar` | Inicia el workflow completo |
| `/urls <url1> <url2>` | Usar URLs de TikTok específicas |
| `/estado` | Ver el estado actual del proceso |
| `/cancelar` | Cancelar el proceso en curso |

### Flujo del bot

```
/trabajar
  → ¿Formato? (YouTube Video / Reel)
  → ¿Qué buscar? (categoría / texto libre / pegar links)
  → ¿De qué país?
  → Busca y filtra clips en TikTok
  → Aprobás los clips
  → Compilación con progreso en tiempo real
  → Preview del video + thumbnail + SEO
  → Horario de publicación (hoy si es buen día)
  → Sube a YouTube con thumbnail incluido
```

---

## Estructura del proyecto

```
youtube_ai_manager/
├── app.py                    # App principal Streamlit
├── run_bot.py                # Bot de Telegram + scheduler
├── requirements.txt
├── .env.example              # Plantilla de variables de entorno
├── client_secret.json        # NO subir a GitHub — OAuth2 Google
├── modules/
│   ├── telegram_bot.py       # Bot y workflow automático
│   ├── workflow.py           # Orquestador: compilar → SEO → YouTube
│   ├── selector_ia.py        # Búsqueda y selección de clips TikTok
│   ├── compilador.py         # Compilación de video con FFmpeg
│   ├── seo_gen.py            # Generador de SEO con Claude
│   ├── thumbnail_gen.py      # Thumbnail con texto overlay (Pillow)
│   ├── uploader.py           # Subida a YouTube (OAuth2)
│   ├── historial.py          # Historial de videos subidos
│   ├── analyzer.py           # Análisis del canal
│   ├── trends.py             # Tendencias virales
│   ├── content_gen.py        # Generador de contenido IA
│   ├── voice_gen.py          # Texto a voz (ElevenLabs)
│   ├── video_editor.py       # Editor de video
│   ├── downloader.py         # Descargador de TikTok
│   ├── tiktok_feed.py        # Feed de TikTok
│   ├── calendario.py         # Calendario de publicaciones
│   └── flujo_estado.py       # Estado del workflow
└── data/                     # NO subir — historial personal
```

---

## Seguridad

Nunca subas estos archivos a GitHub:
- `.env` — contiene tus API keys
- `client_secret.json` — credenciales OAuth2 de Google
- `token_youtube.json` — token de acceso a YouTube
- `data/` — historial personal de videos

Todos están en `.gitignore` por defecto.
