from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json
from datetime import datetime

# Creamos la app — igual que "const app = express()" en Node.js
app = FastAPI(title="YouTube AI Manager API")

# CORS: le decimos al servidor que acepte pedidos desde React
# Sin esto, el navegador bloquea la comunicación frontend ↔ backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # puerto donde corre Vite
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Ruta al archivo de datos (sube un nivel desde /api hasta la raíz del proyecto)
DATA_DIR = Path(__file__).parent.parent / "data"


def leer_json(nombre_archivo: str):
    """Lee un archivo JSON de la carpeta data/ y lo devuelve como dict/list."""
    archivo = DATA_DIR / nombre_archivo
    if not archivo.exists():
        return []
    with open(archivo, "r", encoding="utf-8") as f:
        return json.load(f)


# --- ENDPOINTS ---
# Un endpoint es una URL que el frontend puede llamar para pedir datos

@app.get("/api/videos")
def get_videos():
    """
    Devuelve la lista de videos subidos a YouTube.
    El frontend lo llama con: fetch('http://localhost:8000/api/videos')
    """
    videos = leer_json("youtube_videos.json")

    # Ordenamos por fecha, el más reciente primero
    videos_ordenados = sorted(
        videos,
        key=lambda v: v.get("fecha", ""),
        reverse=True
    )
    return videos_ordenados


@app.get("/api/stats")
def get_stats():
    """
    Devuelve estadísticas generales del canal.
    """
    videos = leer_json("youtube_videos.json")

    total = len(videos)
    ultimo_video = None

    if videos:
        # Encontramos el video más reciente
        ordenados = sorted(videos, key=lambda v: v.get("fecha", ""), reverse=True)
        ultimo = ordenados[0]
        ultimo_video = {
            "titulo": ultimo.get("titulo", ""),
            "url": ultimo.get("url", ""),
            "fecha": ultimo.get("fecha", ""),
        }

    return {
        "total_videos": total,
        "ultimo_video": ultimo_video,
        "canal": "ViralVideos",
    }


# Endpoint de prueba — para verificar que el servidor está vivo
@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
