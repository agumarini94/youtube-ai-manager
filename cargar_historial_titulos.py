"""
Carga inicial del historial de títulos — se corre UNA vez, a mano.

Lee los títulos de los últimos videos del canal desde la API de YouTube (la playlist
de uploads) y los guarda en data/youtube_videos.json sin duplicar los que ya estén.

Hace falta porque el autopilot elige el título que menos se parece a los últimos 100
ya subidos, y hasta ahora el historial solo tenía los videos subidos desde Streamlit.
Sin esta carga, las primeras corridas compararían contra un puñado de títulos viejos.

SOLO LECTURA en YouTube: usa channels().list y playlistItems().list, nada más. No
sube, no edita ni borra nada, y tampoco reescribe token_youtube.json.

Uso:
    python cargar_historial_titulos.py              # últimos 200 videos
    python cargar_historial_titulos.py 50           # últimos 50
    python cargar_historial_titulos.py --dry-run    # mostrar sin guardar
"""

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).parent
# data/youtube_videos.json se resuelve relativo al cwd en modules/historial.py.
os.chdir(RAIZ)
sys.path.insert(0, str(RAIZ))

TOKEN_FILE = RAIZ / "token_youtube.json"
MAX_POR_PAGINA = 50


def _cliente_youtube():
    """Cliente de la API con el token que ya dejó autenticar_youtube.py."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        raise RuntimeError(
            f"No se encontró {TOKEN_FILE.name}. Corré antes: python autenticar_youtube.py"
        )

    from modules.uploader import SCOPES

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        # No se persiste el token: este script es de solo lectura.
        creds.refresh(Request())
    if not creds.valid:
        raise RuntimeError(
            "Credenciales de YouTube inválidas o expiradas. Corré: python autenticar_youtube.py"
        )
    return build("youtube", "v3", credentials=creds)


def leer_titulos_del_canal(youtube, maximo: int) -> list[dict]:
    """
    Recorre la playlist de uploads del canal y devuelve hasta `maximo` videos, del
    más nuevo al más viejo, con las keys que espera historial.agregar_videos_youtube().
    """
    canal = youtube.channels().list(part="contentDetails,snippet", mine=True).execute()
    items = canal.get("items") or []
    if not items:
        raise RuntimeError(
            "La API no devolvió ningún canal para este token. Revisá que el token sea "
            "el de la cuenta de marca del canal, no el de la cuenta personal."
        )

    nombre = items[0].get("snippet", {}).get("title", "?")
    uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    print(f"📺 Canal: {nombre} · playlist de uploads: {uploads_id}")

    videos: list[dict] = []
    page_token = None
    while len(videos) < maximo:
        resp = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_id,
            maxResults=min(MAX_POR_PAGINA, maximo - len(videos)),
            pageToken=page_token,
        ).execute()

        pagina = resp.get("items", [])
        if not pagina:
            break

        for item in pagina:
            snippet = item.get("snippet", {})
            video_id = snippet.get("resourceId", {}).get("videoId")
            titulo = (snippet.get("title") or "").strip()
            if not video_id or not titulo:
                continue
            videos.append({
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "titulo": titulo,
                "fecha": snippet.get("publishedAt", ""),
            })

        print(f"   … {len(videos)} videos leídos")
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return videos


def main() -> int:
    args = [a for a in sys.argv[1:]]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    maximo = 200
    if args:
        try:
            maximo = int(args[0])
        except ValueError:
            print(f"❌ Cantidad inválida: {args[0]!r}. Usá un número, ej: 200")
            return 1
    if maximo < 1:
        print("❌ La cantidad tiene que ser mayor a 0.")
        return 1

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        youtube = _cliente_youtube()
        videos = leer_titulos_del_canal(youtube, maximo)
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ Falló la lectura desde YouTube: {e}")
        return 1

    if not videos:
        print("⚠️  El canal no devolvió videos. No hay nada que cargar.")
        return 0

    from modules.historial import agregar_videos_youtube, ultimos_titulos

    antes = len(ultimos_titulos(10_000))
    print(f"\n📋 Leídos {len(videos)} videos del canal. Historial actual: {antes} títulos.")

    if dry_run:
        print("\n🧪 Dry-run — no se guarda nada. Primeros 10 títulos leídos:")
        for v in videos[:10]:
            print(f"   · {v['titulo']}")
        return 0

    agregados = agregar_videos_youtube(videos)
    despues = len(ultimos_titulos(10_000))

    print(f"✅ Agregados {agregados} títulos nuevos ({len(videos) - agregados} ya estaban).")
    print(f"   Historial: {antes} → {despues} títulos.")
    print("\nListo. El autopilot ya compara contra los últimos 100 de esta lista.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
