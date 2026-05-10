# Cómo funciona YouTube AI Manager
### Guía para entender el proyecto desde cero

---

## Antes de empezar: la idea central

Imaginate que tenés que hacer esto a mano cada vez que querés subir un video:

1. Buscar videos virales en TikTok
2. Descargarlos uno por uno
3. Unirlos en un solo video con algún programa
4. Escribir un título, descripción y hashtags para YouTube
5. Crear una miniatura (thumbnail)
6. Entrar a YouTube Studio y subir todo

Esta app hace **todo eso automáticamente**, desde tu teléfono con un bot de Telegram.

---

## Parte 1 — Conceptos básicos que necesitás entender

### ¿Qué es Python?

Python es un lenguaje de programación. Un lenguaje de programación es básicamente un idioma que las computadoras entienden. Así como le escribís instrucciones a alguien en español, le escribís instrucciones a la computadora en Python.

Ejemplo de Python:
```python
nombre = "Agustin"
print("Hola " + nombre)
# Resultado: Hola Agustin
```

Python es uno de los más populares del mundo porque se lee casi como inglés normal y es ideal para trabajar con APIs e inteligencia artificial.

---

### ¿Qué es una librería o módulo?

Cuando escribís código, no necesitás inventar todo desde cero. Otros programadores ya resolvieron problemas comunes y los empaquetaron en "librerías" que podés usar gratis.

Es como comprar muebles en IKEA en vez de construirlos desde cero.

Ejemplos de librerías que usa este proyecto:

| Librería | Para qué sirve |
|---|---|
| `anthropic` | Hablar con Claude (la IA de Anthropic) |
| `streamlit` | Crear la interfaz web sin saber HTML |
| `python-telegram-bot` | Crear el bot de Telegram |
| `yt-dlp` | Descargar videos de TikTok |
| `Pillow` | Editar imágenes (las miniaturas) |
| `ffmpeg-python` | Unir y editar videos |

Todas estas librerías están listadas en el archivo `requirements.txt`. Cuando ejecutaste `pip install -r requirements.txt`, instalaste todas de una vez.

---

### ¿Qué es una API?

API significa "Application Programming Interface". Es la forma en que dos programas se comunican entre sí.

**Analogía:** Pensá en un restaurante.
- Vos (el cliente) = tu app
- El mozo = la API
- La cocina = el servicio externo (YouTube, Claude, etc.)

Vos le pedís algo al mozo (la API), él va a la cocina (el servidor), y te trae el resultado. Nunca entrás a la cocina directamente.

**Ejemplos concretos en tu proyecto:**
- Tu app le pregunta a la **API de YouTube**: "¿Cuántos suscriptores tiene mi canal?"
- Tu app le pregunta a la **API de Claude**: "Generame un título SEO para este video"
- Tu app le dice a la **API de ElevenLabs**: "Convertí este texto a voz"

---

### ¿Qué es una API Key?

Cuando usás una API, el servicio necesita saber quién sos para cobrarte, limitarte, o autorizarte. La API Key es como tu contraseña personal para ese servicio.

**Por eso nunca la subís a GitHub.** Si alguien la encuentra, puede usar la API a tu nombre y a tu costo.

En tu proyecto las API Keys viven en el archivo `.env`:
```
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxx
YOUTUBE_API_KEY=AIzaxxxxxxxxxx
ELEVENLABS_API_KEY=xxxxxxxxxx
```

---

### ¿Qué es OAuth2?

OAuth2 es un sistema de autenticación más complejo que una simple API Key. Lo usás cuando una app necesita hacer cosas en tu nombre en otro servicio.

**Analogía:** Es como darle las llaves de tu casa a alguien de confianza, pero en vez de las llaves de todo, le das una llave especial que solo abre la puerta del garage.

En tu proyecto, OAuth2 se usa para que la app pueda **subir videos a tu canal de YouTube** sin que tengas que darle tu contraseña de Google. Google te pregunta "¿autorizás a esta app?", vos decís que sí, y Google le da a la app un permiso temporal.

Los archivos relacionados:
- `client_secret.json` → las credenciales de tu "app" en Google Cloud
- `token_youtube.json` → el permiso temporal que Google generó cuando autorizaste

Ambos están en `.gitignore` por razones de seguridad.

---

### ¿Qué es el entorno virtual (venv)?

Imaginá que instalás una librería para este proyecto, pero otra versión de la misma librería para otro proyecto. Entrarían en conflicto.

El entorno virtual es una carpeta aislada donde instalás las librerías **solo para este proyecto**, sin que afecten al resto de tu computadora.

```bash
python3 -m venv venv       # Crea la carpeta del entorno virtual
source venv/bin/activate   # Lo activa (en Mac/Linux)
pip install -r requirements.txt  # Instala las librerías dentro del entorno
```

La carpeta `venv/` no se sube a GitHub porque pesa mucho y cada uno la crea en su propia computadora.

---

### ¿Qué es el archivo .env?

Es un archivo de texto simple que guarda tus variables de entorno (configuraciones privadas). Se llama `.env` y empieza con un punto porque en Mac/Linux eso hace que sea un archivo oculto.

```
ANTHROPIC_API_KEY=sk-ant-123456
TELEGRAM_TOKEN=bot123456:ABCdef
HORA_AUTO=18
```

Tu código lee estas variables con una librería llamada `python-dotenv`. Así el código nunca tiene tus contraseñas escritas "a fuego" — las lee del archivo externo.

---

## Parte 2 — La arquitectura del proyecto

### Cómo se ve todo junto

```
Tu teléfono (Telegram)
       ↓
   run_bot.py          ← arranca el bot y escucha mensajes
       ↓
   workflow.py         ← orquesta todo el proceso
       ↓
┌──────────────────────────────────────┐
│  selector_ia.py  → busca en TikTok  │
│  downloader.py   → descarga clips   │
│  compilador.py   → une los videos   │
│  seo_gen.py      → genera SEO       │ ← cada módulo hace UNA cosa
│  thumbnail_gen.py → crea miniatura  │
│  uploader.py     → sube a YouTube   │
└──────────────────────────────────────┘
       ↑
   app.py              ← interfaz web alternativa (Streamlit)
```

---

### ¿Qué hace cada archivo?

#### `app.py` — La interfaz web
Es el archivo principal de la app visual. Cuando ejecutás `streamlit run app.py`, este archivo genera la página web que ves en el navegador.

Streamlit es especial: escribís código Python y él solo convierte todo en una web con botones, formularios y gráficos. No necesitás saber HTML ni CSS.

```python
import streamlit as st

st.title("YouTube AI Manager")        # Muestra un título en la web
st.button("Generar SEO")              # Crea un botón clickeable
```

---

#### `run_bot.py` — El cerebro del bot de Telegram
Arranca el bot y lo mantiene escuchando mensajes. Cuando escribís `/trabajar` en Telegram, este archivo recibe el mensaje y llama al workflow.

También tiene un scheduler (programador de tareas) que puede ejecutar el workflow automáticamente todos los días a la hora que configuraste en `.env`.

---

#### `requirements.txt` — La lista de compras
Lista todas las librerías que necesita el proyecto. Cuando alguien clona tu proyecto, ejecuta `pip install -r requirements.txt` y tiene todo instalado.

```
anthropic==0.21.3
streamlit==1.32.0
python-telegram-bot==20.7
...
```

---

#### `modules/` — Los módulos especializados

Cada archivo en esta carpeta hace una sola cosa. Esto se llama "separación de responsabilidades" y es una buena práctica de programación.

---

**`modules/selector_ia.py` — Busca clips en TikTok**

Recibe una búsqueda (por ejemplo "funny cats") y encuentra videos virales de TikTok. Le pide a Claude que evalúe cuáles son mejores según la cantidad de likes, comentarios y duración.

---

**`modules/downloader.py` — Descarga los videos**

Usa la librería `yt-dlp` para descargar los clips de TikTok que el usuario aprobó. `yt-dlp` es una herramienta open source que sabe descargar videos de cientos de plataformas.

---

**`modules/compilador.py` — Une los videos**

Toma todos los clips descargados y los une en un solo video usando FFmpeg. FFmpeg es un programa muy poderoso de línea de comandos para procesar audio y video. Tu código Python simplemente le da instrucciones a FFmpeg.

```
clip1.mp4 + clip2.mp4 + clip3.mp4 → video_final.mp4
```

---

**`modules/seo_gen.py` — Genera el SEO con Claude**

Le manda el video (o una descripción) a Claude y le pide que genere:
- Un título optimizado para YouTube
- Una descripción con keywords
- Tags relevantes

"SEO" significa Search Engine Optimization — técnicas para que YouTube muestre tu video a más gente.

---

**`modules/thumbnail_gen.py` — Crea la miniatura**

Extrae el mejor frame (imagen) del video y le superpone texto con la librería Pillow. El texto también lo genera Claude.

```
frame_del_video.jpg + texto generado por IA → thumbnail.jpg
```

---

**`modules/uploader.py` — Sube a YouTube**

Usa la API de YouTube (con las credenciales OAuth2) para subir el video con todos sus metadatos: título, descripción, tags, miniatura, categoría, y hora de publicación.

---

**`modules/workflow.py` — El orquestador**

Es el director de orquesta. No hace nada por sí solo, pero llama a todos los otros módulos en el orden correcto:

```
selector_ia → downloader → compilador → seo_gen → thumbnail_gen → uploader
```

---

**`modules/telegram_bot.py` — La interfaz de Telegram**

Maneja la conversación con el usuario en Telegram. Recibe comandos, hace preguntas, muestra botones de aprobación, y llama al workflow cuando corresponde.

---

**`modules/historial.py` — Guarda un registro**

Cada vez que se sube un video, guarda en un archivo JSON (en la carpeta `data/`) el título, la fecha, el link, y otros datos. Así podés ver el historial de todo lo que subiste.

---

**`modules/voice_gen.py` — Genera voz**

Toma texto (por ejemplo el guión de un video) y lo convierte a audio usando la API de ElevenLabs. El resultado es un archivo `.mp3`.

---

## Parte 3 — El flujo completo explicado en palabras simples

Esto es lo que pasa cuando escribís `/trabajar` en Telegram:

**1. Telegram recibe el comando**
`telegram_bot.py` detecta el mensaje `/trabajar` y empieza la conversación.

**2. Te pregunta qué formato querés**
¿Un video normal de YouTube o un Reel corto?

**3. Te pregunta qué buscar**
Podés escribir una categoría ("animales graciosos"), texto libre ("videos de fútbol argentino"), o pegar links directos de TikTok.

**4. Busca clips**
`selector_ia.py` busca en TikTok y Claude evalúa cuáles son mejores.

**5. Te muestra los clips para aprobar**
Ves los links y elegís cuáles querés incluir.

**6. Descarga los clips**
`downloader.py` los descarga a tu computadora.

**7. Los une en un video**
`compilador.py` llama a FFmpeg y genera `video_final.mp4`.

**8. Genera el SEO**
`seo_gen.py` le manda el video a Claude y recibe título, descripción y tags.

**9. Crea la miniatura**
`thumbnail_gen.py` extrae un frame y le pone texto encima.

**10. Te muestra todo para aprobar**
Ves el video, la miniatura y el SEO antes de publicar.

**11. Sube a YouTube**
`uploader.py` sube todo a tu canal con OAuth2.

**12. Te manda el link**
Telegram te manda el link al video publicado.

---

## Parte 4 — Cómo se creó este proyecto (con IA)

Este proyecto fue construido con la ayuda de Claude (la misma IA que usamos para generar el SEO). El proceso fue:

1. **Definir qué querías hacer** — "quiero automatizar mi canal de YouTube"
2. **Dividir el problema en partes** — cada módulo es una parte del problema
3. **Pedirle a Claude que escriba cada parte** — con instrucciones muy específicas
4. **Probar, ver errores, y corregir** — muchas veces con la ayuda de Claude
5. **Conectar todas las partes** — el `workflow.py` y el bot conectan todo

Esto se llama **"vibe coding"** o desarrollo asistido por IA, y es una habilidad real y valorada en 2024-2025. Lo importante no es memorizar cada línea de código, sino:

- Saber qué problema estás resolviendo
- Entender cómo se divide el problema
- Saber leer el código y entender qué hace cada parte
- Poder debuggear (encontrar errores) con ayuda de la IA

---

## Parte 5 — Glosario rápido

| Término | Explicación simple |
|---|---|
| **API** | Forma en que dos programas se hablan |
| **API Key** | Contraseña para usar una API |
| **OAuth2** | Sistema para dar permisos sin dar la contraseña |
| **Librería** | Código escrito por otros que podés reutilizar |
| **Módulo** | Un archivo Python que hace una tarea específica |
| **Entorno virtual** | Carpeta aislada con las librerías del proyecto |
| **`.env`** | Archivo con variables privadas (contraseñas, keys) |
| **`.gitignore`** | Lista de archivos que NO se suben a GitHub |
| **Streamlit** | Librería para hacer apps web con Python |
| **FFmpeg** | Programa para procesar audio y video |
| **SEO** | Técnicas para aparecer más en búsquedas |
| **Scheduler** | Programa que ejecuta tareas a horarios fijos |
| **JSON** | Formato de texto para guardar datos estructurados |
| **Token** | Permiso temporal generado después de autenticarse |
| **Commit** | Guardar una versión del código en git |
| **Push** | Subir el código de tu computadora a GitHub |

---

## Parte 6 — Cómo seguir aprendiendo

Si querés entender más profundamente el código, el camino más efectivo es:

1. **Abrir un módulo específico** (por ejemplo `modules/seo_gen.py`) y pedirle a Claude que te lo explique línea por línea
2. **Hacer pequeños cambios** y ver qué pasa — por ejemplo cambiar el texto del título que genera Claude
3. **Leer los errores** cuando algo falla — los mensajes de error de Python siempre te dicen exactamente qué salió mal y en qué línea

El mejor aprendizaje viene de **romper cosas y arreglarlas**.

---

*Esta guía fue escrita para entender el proyecto YouTube AI Manager.*
*Última actualización: Mayo 2025*
