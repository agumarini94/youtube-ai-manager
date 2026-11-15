"""
Autenticación de YouTube desde la terminal — sin Streamlit.

Uso:
    python autenticar_youtube.py

Abre el navegador, te deja elegir la cuenta de Google y la cuenta de marca,
y guarda token_youtube.json en la carpeta desde la que se ejecuta el script.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from modules.uploader import SCOPES, TOKEN_FILE, autenticar_youtube_local


def main() -> int:
    print("🔐 Autenticación con YouTube")
    print(f"   Scopes: {', '.join(s.rsplit('/', 1)[-1] for s in SCOPES)}")
    print(f"   Token destino: {Path(TOKEN_FILE).resolve()}")
    print("\nSe va a abrir el navegador.")
    print("Elegí agumarini94@gmail.com y después la cuenta de marca SunsetVideos.\n")

    try:
        credenciales = autenticar_youtube_local()
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1
    except Exception as e:
        print(f"❌ Falló la autenticación: {e}")
        return 1

    print(f"\n✅ Listo. Token guardado en {Path(TOKEN_FILE).resolve()}")
    print(f"   Refresh token: {'sí' if credenciales.refresh_token else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
