"""
Módulo — Generador de Thumbnail con texto overlay
Extrae el frame de mayor acción del video y superpone título usando Pillow.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# Fuentes del sistema macOS, en orden de preferencia
_FUENTES_SISTEMA = [
    "/System/Library/Fonts/HelveticaNeue-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/SFNSDisplay-Bold.otf",
]


def _fuente(tamaño: int, negrita: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    fuentes = _FUENTES_SISTEMA if negrita else _FUENTES_SISTEMA[1:]
    for ruta in fuentes:
        if Path(ruta).exists():
            try:
                return ImageFont.truetype(ruta, tamaño)
            except Exception:
                continue
    return ImageFont.load_default()


def _duracion(ruta: str) -> float:
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", ruta],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip() or "60")
    except Exception:
        return 60.0


def extraer_frame(ruta_video: str, carpeta: str, porcentaje: float = 0.25) -> str | None:
    """
    Extrae el frame más llamativo del video.
    Primero intenta con scene detection; si no hay cambios de escena, usa el porcentaje indicado.
    """
    dur = _duracion(ruta_video)

    # Intento 1: frame de escena con cambio visual destacado (scene score > 0.3)
    scene_dir = Path(carpeta) / "scenes"
    scene_dir.mkdir(exist_ok=True)
    pat = str(scene_dir / "sc_%04d.jpg")
    try:
        subprocess.run(
            [FFMPEG, "-i", ruta_video,
             "-vf", "select='gt(scene,0.30)',scale=1280:720:force_original_aspect_ratio=decrease",
             "-vsync", "0", "-frames:v", "6", "-q:v", "2", pat, "-y"],
            capture_output=True, timeout=40,
        )
        frames = sorted(scene_dir.glob("sc_*.jpg"))
        if frames:
            # Segundo frame si hay varios: el primero suele ser el corte de apertura
            return str(frames[min(1, len(frames) - 1)])
    except Exception:
        pass

    # Fallback: frame en el porcentaje indicado
    ruta_fb = os.path.join(carpeta, "frame_pos.jpg")
    try:
        subprocess.run(
            [FFMPEG, "-ss", str(dur * porcentaje), "-i", ruta_video,
             "-frames:v", "1", "-q:v", "2",
             "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
             ruta_fb, "-y"],
            capture_output=True, timeout=20,
        )
        if Path(ruta_fb).exists() and Path(ruta_fb).stat().st_size > 1000:
            return ruta_fb
    except Exception:
        pass

    return None


def _superponer_texto(
    img: Image.Image,
    texto: str,
    y_base: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color_texto: tuple,
    opacidad_fondo: int = 145,
    padding: int = 16,
) -> Image.Image:
    """
    Superpone texto centrado con fondo semitransparente sobre img (RGB).
    Retorna la imagen modificada (RGB).
    """
    w, h = img.size

    # Medir texto
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = dummy.textbbox((0, 0), texto, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x = (w - tw) // 2
    y = y_base - th

    # Rectángulo de fondo semitransparente usando alpha_composite
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    rect = [x - padding, y - padding, x + tw + padding, y + th + padding]
    ov_draw.rectangle(rect, fill=(0, 0, 0, opacidad_fondo))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # Dibujar texto (sombra + color)
    draw = ImageDraw.Draw(img)
    draw.text((x + 2, y + 2), texto, font=font, fill=(0, 0, 0))
    draw.text((x, y), texto, font=font, fill=color_texto)

    return img


def aplicar_texto(
    ruta_frame: str,
    ruta_salida: str,
    titulo: str,
    subtitulo: str = "",
    ancho: int = 1280,
    alto: int = 720,
) -> tuple[bool, str]:
    """
    Abre el frame, lo escala a ancho×alto y superpone el texto con Pillow.
    Retorna (ok, error).
    """
    try:
        img = Image.open(ruta_frame).convert("RGB")

        # Escalar con crop centrado para cubrir exactamente ancho×alto
        ratio_w = ancho / img.width
        ratio_h = alto / img.height
        escala = max(ratio_w, ratio_h)
        nuevo_w = int(img.width * escala)
        nuevo_h = int(img.height * escala)
        img = img.resize((nuevo_w, nuevo_h), Image.LANCZOS)

        left = (nuevo_w - ancho) // 2
        top = (nuevo_h - alto) // 2
        img = img.crop((left, top, left + ancho, top + alto))

        margen_inferior = 28

        if titulo:
            tam = 70
            font_titulo = _fuente(tam, negrita=True)
            # Reducir tamaño si el texto es muy largo
            dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            while tam > 32:
                bbox = dummy.textbbox((0, 0), titulo, font=font_titulo)
                if (bbox[2] - bbox[0]) < ancho - 80:
                    break
                tam -= 4
                font_titulo = _fuente(tam, negrita=True)

            dummy2 = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            bbox = dummy2.textbbox((0, 0), titulo, font=font_titulo)
            th = bbox[3] - bbox[1]

            img = _superponer_texto(
                img, titulo,
                y_base=alto - margen_inferior,
                font=font_titulo,
                color_texto=(255, 255, 255),
                opacidad_fondo=150,
                padding=18,
            )
            margen_inferior += th + 52

        if subtitulo:
            tam_s = 42
            font_sub = _fuente(tam_s, negrita=False)
            dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            while tam_s > 24:
                bbox = dummy.textbbox((0, 0), subtitulo, font=font_sub)
                if (bbox[2] - bbox[0]) < ancho - 80:
                    break
                tam_s -= 4
                font_sub = _fuente(tam_s, negrita=False)

            img = _superponer_texto(
                img, subtitulo,
                y_base=alto - margen_inferior,
                font=font_sub,
                color_texto=(255, 230, 0),
                opacidad_fondo=120,
                padding=12,
            )

        img.save(ruta_salida, "JPEG", quality=92)
        return True, ""

    except Exception as e:
        return False, str(e)


# ── Generación automática (headless, sin Streamlit) ───────────────────────────

def generar_texto_thumbnail(seo: dict) -> dict:
    """
    Pide a Claude una frase corta y específica para superponer en el thumbnail.
    Usa el SEO ya generado — Claude sabe exactamente de qué trata el video.
    Retorna {'titulo_thumb': str, 'subtitulo_thumb': str}.
    """
    import anthropic as _anthropic

    titulo = seo.get("titulo", "")
    descripcion = (seo.get("descripcion") or "")[:300]
    api_key = os.getenv("ANTHROPIC_API_KEY")

    fallback_titulo = " ".join(titulo.upper().split()[:4])
    if not api_key:
        return {"titulo_thumb": fallback_titulo, "subtitulo_thumb": ""}

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Sos experto en CTR de YouTube. Generás el texto del thumbnail "
                f"para un video de compilación viral.\n\n"
                f"TÍTULO del video: {titulo}\n"
                f"DESCRIPCIÓN (fragmento): {descripcion}\n\n"
                f"Creá el texto del thumbnail. Debe generar máxima curiosidad "
                f"y ser ESPECÍFICO al contenido (no genérico).\n\n"
                f"Ejemplos buenos según contenido:\n"
                f"- Caídas → 'SE CAYÓ 3 VECES 💀' / 'EL FAIL MÁS ÉPICO 😂'\n"
                f"- Animales → 'ESTE PERRO ES UN GENIO 🤣' / 'NO LO VAS A CREER 😱'\n"
                f"- Deportes → 'GOL IMPOSIBLE 🔥' / 'EL MEJOR MOMENTO 😮'\n"
                f"- Humor → 'ME DUELE EL ESTÓMAGO 😭' / 'DEMASIADO GRACIOSO 💀'\n\n"
                f"Respondé SOLO con JSON válido:\n"
                f'{{"titulo_thumb": "3-5 PALABRAS MAYÚSC + 1 emoji", '
                f'"subtitulo_thumb": "2-3 palabras opcionales o vacío"}}'
            )}],
        )
        data = json.loads(resp.content[0].text.strip())
        return {
            "titulo_thumb": (data.get("titulo_thumb") or fallback_titulo)[:50],
            "subtitulo_thumb": (data.get("subtitulo_thumb") or "")[:40],
        }
    except Exception:
        return {"titulo_thumb": fallback_titulo, "subtitulo_thumb": ""}


def generar_thumbnail_automatico(ruta_video: str, seo: dict, carpeta: str | None = None) -> str | None:
    """
    Pipeline headless: extrae el mejor frame → pide texto a Claude → aplica overlay.
    Retorna la ruta del thumbnail generado, o None si falla.
    """
    try:
        if carpeta is None:
            carpeta = tempfile.mkdtemp(prefix="thumb_auto_")
        Path(carpeta).mkdir(parents=True, exist_ok=True)

        frame = extraer_frame(ruta_video, carpeta, porcentaje=0.25)
        if not frame:
            return None

        texto = generar_texto_thumbnail(seo)
        salida = os.path.join(carpeta, "thumbnail_auto.jpg")
        ok, _ = aplicar_texto(
            frame, salida,
            titulo=texto.get("titulo_thumb", ""),
            subtitulo=texto.get("subtitulo_thumb", ""),
        )
        return salida if ok else None
    except Exception:
        return None


# ── UI Streamlit ───────────────────────────────────────────────────────────────

def mostrar_thumbnail_gen():
    st.title("🖼️ Generador de Thumbnail")
    st.markdown("Extraé el frame más épico del video y agregale un título impactante. Listo para subir a YouTube.")

    with st.expander("❓ ¿Por qué importa el thumbnail?"):
        st.markdown("""
El thumbnail es responsable del **~70 % del CTR** en YouTube.
- Con texto bold y fondo contrastante los clicks aumentan hasta **2×**
- Este módulo detecta automáticamente el frame con más acción del video
- Exporta en **1280×720 px** (el formato estándar de YouTube)

**Tip:** usá el título que generó el SEO — ya está optimizado para engagement.
        """)

    # ── 1. Video fuente ────────────────────────────────────────────────────────
    st.subheader("1. Video fuente")

    ruta_auto = ""
    for key in ("comp_resultado_ruta", "video_compilado_ruta", "ve_ruta"):
        ruta = st.session_state.get(key, "")
        if ruta and Path(ruta).exists():
            ruta_auto = ruta
            break

    ruta_video: str | None = None

    if ruta_auto:
        st.success(f"✅ Video disponible: `{Path(ruta_auto).name}`")
        fuente = st.radio(
            "Video a usar",
            ["Usar el video compilado", "Subir otro video"],
            horizontal=True,
            key="thumb_fuente",
        )
        if fuente == "Usar el video compilado":
            ruta_video = ruta_auto
    else:
        fuente = "Subir otro video"
        st.info("Compilá un video primero, o subí uno manualmente.")

    if fuente == "Subir otro video" or not ruta_auto:
        archivo = st.file_uploader(
            "Subí el video",
            type=["mp4", "mov", "avi", "mkv"],
            key="thumb_upload",
        )
        if archivo:
            d = Path(tempfile.gettempdir()) / "thumb_uploads"
            d.mkdir(exist_ok=True)
            ruta_video = str(d / archivo.name)
            with open(ruta_video, "wb") as f:
                f.write(archivo.getvalue())

    if not ruta_video:
        return

    # Guardar en session_state para que el SEO y el subidor lo encuentren
    st.session_state["video_compilado_ruta"] = ruta_video
    st.session_state["comp_resultado_ruta"] = ruta_video
    st.session_state["video_para_subir"] = ruta_video

    # ── 2. Texto ───────────────────────────────────────────────────────────────
    st.subheader("2. Texto del thumbnail")

    titulo_seo = st.session_state.get("ultimo_titulo", "")
    if not titulo_seo and "seo_resultado" in st.session_state:
        titulo_seo = st.session_state["seo_resultado"].get("titulo", "")

    titulo = st.text_input(
        "Título principal (texto grande, parte baja de la imagen)",
        value=titulo_seo[:65] if titulo_seo else "",
        max_chars=65,
        placeholder="¡NO VAS A CREER LO QUE PASÓ!",
        key="thumb_titulo",
    )
    subtitulo = st.text_input(
        "Subtítulo opcional (texto amarillo sobre el título)",
        max_chars=80,
        placeholder="Compilación viral 2025",
        key="thumb_subtitulo",
    )

    # ── 3. Frame ───────────────────────────────────────────────────────────────
    st.subheader("3. Frame a usar")

    modo = st.radio(
        "Modo de selección",
        ["Automático (frame de mayor acción)", "Posición manual en el video"],
        horizontal=True,
        key="thumb_modo",
    )

    porcentaje = 0.25
    if modo == "Posición manual en el video":
        pct = st.slider(
            "Posición en el video",
            0, 95, 25, 5,
            format="%d%%",
            key="thumb_pct",
            help="0 % = inicio · 25 % = primer cuarto · 50 % = mitad",
        )
        porcentaje = pct / 100

    # ── Generar ────────────────────────────────────────────────────────────────
    st.markdown("---")
    if st.button("🖼️ Generar thumbnail", type="primary", use_container_width=True):
        if not titulo.strip():
            st.warning("⚠️ Escribí un título para el thumbnail.")
            st.stop()

        carpeta = tempfile.mkdtemp(prefix="thumb_")

        with st.spinner("Extrayendo el mejor frame del video..."):
            frame = extraer_frame(ruta_video, carpeta, porcentaje)

        if not frame:
            st.error("❌ No se pudo extraer el frame. Probá con modo manual.")
            st.stop()

        with st.spinner("Aplicando texto al thumbnail..."):
            salida = os.path.join(carpeta, "thumbnail.jpg")
            ok, err = aplicar_texto(frame, salida, titulo, subtitulo)

        if not ok:
            st.error(f"❌ Error al generar thumbnail: {err}")
            st.stop()

        st.session_state["thumb_resultado"] = salida
        st.session_state["thumb_frame_orig"] = frame

    # ── Resultado ──────────────────────────────────────────────────────────────
    if "thumb_resultado" in st.session_state:
        ruta_th = st.session_state["thumb_resultado"]
        if not Path(ruta_th).exists():
            st.session_state.pop("thumb_resultado", None)
            st.session_state.pop("thumb_frame_orig", None)
            return

        st.success("✅ Thumbnail listo")
        st.image(ruta_th, caption="Thumbnail final — 1280×720 px", use_column_width=True)

        col_a, col_b = st.columns(2)
        with col_a:
            with open(ruta_th, "rb") as f:
                st.download_button(
                    "⬇️ Descargar thumbnail",
                    data=f.read(),
                    file_name="thumbnail_youtube.jpg",
                    mime="image/jpeg",
                    use_container_width=True,
                    type="primary",
                )
        with col_b:
            frame_orig = st.session_state.get("thumb_frame_orig")
            if frame_orig and Path(frame_orig).exists():
                with st.expander("👁️ Frame sin texto"):
                    st.image(frame_orig, caption="Frame original")

        st.caption(
            "💡 Si el frame no te convence, cambiá a **Posición manual** y probá distintos porcentajes "
            "hasta encontrar el momento más épico."
        )

        st.markdown("---")
        st.subheader("¿Qué hacés ahora?")
        col_seo, col_sub, col_reset = st.columns(3)

        with col_seo:
            if st.button("📋 Generar SEO", type="primary", use_container_width=True, key="btn_thumb_seo"):
                st.session_state.modulo_activo = "Generador de SEO"
                st.rerun()

        with col_sub:
            if st.button("🚀 Subir a YouTube", use_container_width=True, key="btn_thumb_subir"):
                st.session_state.modulo_activo = "Subida a YouTube"
                st.rerun()

        with col_reset:
            if st.button("🔄 Nuevo thumbnail", use_container_width=True, key="btn_thumb_reset"):
                st.session_state.pop("thumb_resultado", None)
                st.session_state.pop("thumb_frame_orig", None)
                st.rerun()
