"""Marca de agua — superpone MarcaDeAgua.png en la esquina superior derecha de un reel."""

import subprocess
from pathlib import Path

FFMPEG = "/opt/homebrew/bin/ffmpeg"
_LOGO = Path(__file__).resolve().parent.parent / "MarcaDeAgua.png"

_FILTRO = (
    "[1:v][0:v]scale2ref=w=main_w*0.10:h=-1[wm][base];"
    "[wm]format=rgba,colorchannelmixer=aa=0.10[wma];"
    "[base][wma]overlay=W-w-12:12:format=auto[vout]"
)


def aplicar_marca_de_agua(ruta_video: str) -> bool:
    """Superpone la marca de agua sobre `ruta_video` y lo reemplaza in-place.
    Si algo falla, deja el video original intacto (nunca rompe el pipeline)."""
    if not _LOGO.exists() or not Path(ruta_video).exists():
        return False
    tmp = ruta_video + ".wm.mp4"
    try:
        result = subprocess.run(
            [FFMPEG, "-y", "-i", ruta_video, "-i", str(_LOGO),
             "-filter_complex", _FILTRO,
             "-map", "[vout]", "-map", "0:a?",
             "-c:v", "libx264", "-preset", "fast", "-crf", "20",
             "-c:a", "copy",
             tmp],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0 or not Path(tmp).exists():
            return False
        Path(tmp).replace(ruta_video)
        return True
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        return False
