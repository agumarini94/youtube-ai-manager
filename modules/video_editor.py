"""
Módulo — Editor de Video (Pipeline)
Configurás todas las ediciones y procesás todo de una sola vez.
Requiere FFmpeg instalado: brew install ffmpeg
"""

import streamlit as st
import os
import tempfile
import subprocess
import json
import shutil
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades base
# ─────────────────────────────────────────────────────────────────────────────

def verificar_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def obtener_info_video(ruta: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", ruta],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        info = {"duracion": 0.0, "fps": 0.0, "ancho": 0, "alto": 0, "tiene_audio": False}
        info["duracion"] = float(data.get("format", {}).get("duration", 0))
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                info["ancho"] = stream.get("width", 0)
                info["alto"] = stream.get("height", 0)
                fps_str = stream.get("r_frame_rate", "0/1")
                num, den = fps_str.split("/")
                info["fps"] = round(float(num) / float(den), 2) if float(den) > 0 else 0
            elif stream.get("codec_type") == "audio":
                info["tiene_audio"] = True
        return info
    except Exception:
        return {"duracion": 0.0, "fps": 0.0, "ancho": 0, "alto": 0, "tiene_audio": False}


def segundos_a_tiempo(s: float) -> str:
    h = int(s) // 3600
    m = (int(s) % 3600) // 60
    seg = int(s) % 60
    if h > 0:
        return f"{h}:{m:02d}:{seg:02d}"
    return f"{m:02d}:{seg:02d}"


def tmp_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "yt_editor"
    d.mkdir(exist_ok=True)
    return d


def guardar_upload(archivo, nombre: str = None) -> str:
    ruta = tmp_dir() / (nombre or archivo.name)
    with open(ruta, "wb") as f:
        f.write(archivo.getvalue())
    return str(ruta)


def run_ffmpeg(cmd: list) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr[-800:]
        return True, ""
    except FileNotFoundError:
        return False, "FFmpeg no encontrado. Instalalo con: brew install ffmpeg"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Operaciones individuales (cada una recibe ruta_entrada y devuelve ruta_salida)
# ─────────────────────────────────────────────────────────────────────────────

def op_recortar(entrada: str, salida: str, inicio: float, fin: float) -> tuple[bool, str]:
    """Conserva solo el fragmento entre inicio y fin."""
    cmd = ["ffmpeg", "-i", entrada, "-ss", str(inicio), "-to", str(fin),
           "-c:v", "libx264", "-c:a", "aac", "-y", salida]
    return run_ffmpeg(cmd)


def op_eliminar_tramos(entrada: str, salida: str, tramos: list) -> tuple[bool, str]:
    """Elimina los tramos indicados y une el resto."""
    info = obtener_info_video(entrada)
    duracion = info["duracion"]
    if not duracion:
        return False, "No se pudo leer la duración del video."

    tramos_ord = sorted(tramos, key=lambda x: x[0])
    mantener = []
    cursor = 0.0
    for ini, fin in tramos_ord:
        if cursor < ini - 0.01:
            mantener.append((cursor, ini))
        cursor = max(cursor, fin)
    if cursor < duracion - 0.1:
        mantener.append((cursor, duracion))

    if not mantener:
        return False, "Los tramos cubren todo el video. No quedaría nada."

    if len(mantener) == 1:
        ini, fin = mantener[0]
        cmd = ["ffmpeg", "-i", entrada, "-ss", str(ini), "-to", str(fin),
               "-c:v", "libx264", "-c:a", "aac", "-y", salida]
        return run_ffmpeg(cmd)

    fv, fa = [], []
    for i, (ini, fin) in enumerate(mantener):
        fv.append(f"[0:v]trim={ini}:{fin},setpts=PTS-STARTPTS[v{i}]")
        if info["tiene_audio"]:
            fa.append(f"[0:a]atrim={ini}:{fin},asetpts=PTS-STARTPTS[a{i}]")

    n = len(mantener)
    if info["tiene_audio"]:
        cin = "".join(f"[v{i}][a{i}]" for i in range(n))
        fc = ";".join(fv + fa + [f"{cin}concat=n={n}:v=1:a=1[outv][outa]"])
        cmd = ["ffmpeg", "-i", entrada, "-filter_complex", fc,
               "-map", "[outv]", "-map", "[outa]", "-y", salida]
    else:
        cin = "".join(f"[v{i}]" for i in range(n))
        fc = ";".join(fv + [f"{cin}concat=n={n}:v=1:a=0[outv]"])
        cmd = ["ffmpeg", "-i", entrada, "-filter_complex", fc,
               "-map", "[outv]", "-y", salida]
    return run_ffmpeg(cmd)


def op_insertar_clip(entrada: str, salida: str, clip: str, en_segundo: float) -> tuple[bool, str]:
    """Inserta un clip externo en una posición específica del video."""
    info = obtener_info_video(entrada)
    duracion = info["duracion"]
    d = tmp_dir()
    parte1 = str(d / "ins_p1.mp4")
    parte2 = str(d / "ins_p2.mp4")
    lista = str(d / "ins_list.txt")

    ok, err = op_recortar(entrada, parte1, 0, en_segundo)
    if not ok:
        return False, f"Error cortando parte 1: {err}"

    if en_segundo < duracion - 0.1:
        ok, err = op_recortar(entrada, parte2, en_segundo, duracion)
        if not ok:
            return False, f"Error cortando parte 2: {err}"
        partes = [parte1, clip, parte2]
    else:
        partes = [parte1, clip]

    with open(lista, "w") as f:
        for p in partes:
            f.write(f"file '{p}'\n")

    cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", lista,
           "-c", "copy", "-y", salida]
    return run_ffmpeg(cmd)


def op_audio(
    entrada: str, salida: str,
    quitar: bool = False,
    volumen: float = 1.0,
    reemplazar: str = None,
    bg_music: str = None,
    bg_vol: float = 0.3,
    orig_vol: float = 1.0,
    sil_ini: float = None,
    sil_fin: float = None,
) -> tuple[bool, str]:
    if quitar:
        return run_ffmpeg(["ffmpeg", "-i", entrada, "-an", "-c:v", "copy", "-y", salida])
    if reemplazar:
        return run_ffmpeg(["ffmpeg", "-i", entrada, "-i", reemplazar,
                           "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
                           "-shortest", "-y", salida])
    if bg_music:
        fc = (f"[0:a]volume={orig_vol}[a1];"
              f"[1:a]volume={bg_vol}[a2];"
              f"[a1][a2]amix=inputs=2:duration=first[outa]")
        return run_ffmpeg(["ffmpeg", "-i", entrada, "-i", bg_music,
                           "-filter_complex", fc, "-map", "0:v", "-map", "[outa]",
                           "-c:v", "copy", "-y", salida])
    if sil_ini is not None and sil_fin is not None:
        return run_ffmpeg(["ffmpeg", "-i", entrada,
                           "-af", f"volume=enable='between(t,{sil_ini},{sil_fin})':volume=0",
                           "-c:v", "copy", "-y", salida])
    if volumen != 1.0:
        return run_ffmpeg(["ffmpeg", "-i", entrada, "-af", f"volume={volumen}",
                           "-c:v", "copy", "-y", salida])
    shutil.copy(entrada, salida)
    return True, ""


def op_imagen(
    entrada: str, salida: str,
    rotacion: int = 0, flip_h: bool = False, flip_v: bool = False,
    velocidad: float = 1.0,
    brillo: float = 0.0, contraste: float = 1.0, saturacion: float = 1.0,
    bn: bool = False, fade_in: float = 0.0, fade_out: float = 0.0,
    duracion: float = 0.0, tiene_audio: bool = True,
) -> tuple[bool, str]:
    fv, fa = [], []
    rot_map = {90: "transpose=1", 180: "transpose=2,transpose=2", 270: "transpose=2"}
    if rotacion in rot_map:
        fv.append(rot_map[rotacion])
    if flip_h:
        fv.append("hflip")
    if flip_v:
        fv.append("vflip")
    if velocidad != 1.0:
        fv.append(f"setpts={1/velocidad}*PTS")
        if tiene_audio:
            fa.append(f"atempo={velocidad}")
    eq = []
    if brillo != 0.0:
        eq.append(f"brightness={brillo:.2f}")
    if contraste != 1.0:
        eq.append(f"contrast={contraste:.2f}")
    if saturacion != 1.0:
        eq.append(f"saturation={saturacion:.2f}")
    if eq:
        fv.append(f"eq={':'.join(eq)}")
    if bn:
        fv.append("hue=s=0")
    if fade_in > 0:
        fv.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0 and duracion > 0:
        fv.append(f"fade=t=out:st={max(0, duracion - fade_out):.2f}:d={fade_out}")

    if not fv and not fa:
        shutil.copy(entrada, salida)
        return True, ""

    cmd = ["ffmpeg", "-i", entrada]
    if fv:
        cmd += ["-vf", ",".join(fv)]
    if fa:
        cmd += ["-af", ",".join(fa)]
    cmd += ["-y", salida]
    return run_ffmpeg(cmd)


def op_texto(
    entrada: str, salida: str,
    texto: str, pos_x: str, pos_y: str,
    tamaño: int, color: str,
    inicio: float, fin: float,
) -> tuple[bool, str]:
    txt = texto.replace("'", "\u2019").replace(":", "\\:")
    dt = (f"drawtext=text='{txt}':fontsize={tamaño}:fontcolor={color}"
          f":x={pos_x}:y={pos_y}:enable='between(t,{inicio},{fin})'"
          f":box=1:boxcolor=black@0.4:boxborderw=6")
    return run_ffmpeg(["ffmpeg", "-i", entrada, "-vf", dt,
                       "-codec:a", "copy", "-y", salida])


def op_exportar(entrada: str, salida: str, resolucion: str, solo_audio: bool = False) -> tuple[bool, str]:
    if solo_audio:
        return run_ffmpeg(["ffmpeg", "-i", entrada, "-vn",
                           "-acodec", "libmp3lame", "-q:a", "2", "-y", salida])
    escalas = {"1080p": ("1920", "1080"), "720p": ("1280", "720"), "480p": ("854", "480")}
    w, h = escalas.get(resolucion, ("1280", "720"))
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    return run_ffmpeg(["ffmpeg", "-i", entrada, "-vf", vf,
                       "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                       "-c:a", "aac", "-b:a", "128k", "-y", salida])


# ─────────────────────────────────────────────────────────────────────────────
# Motor del pipeline
# ─────────────────────────────────────────────────────────────────────────────

def ejecutar_pipeline(ruta_original: str, pasos: list) -> tuple[bool, str, str]:
    """
    Ejecuta todos los pasos en orden sobre el video.
    Retorna (éxito, error, ruta_final).
    """
    d = tmp_dir()
    ruta_actual = ruta_original

    for i, paso in enumerate(pasos):
        ruta_sig = str(d / f"pipe_{i:02d}.mp4")
        tipo = paso["tipo"]
        p = paso.get("params", {})

        if tipo == "recortar":
            ok, err = op_recortar(ruta_actual, ruta_sig, p["inicio"], p["fin"])
        elif tipo == "eliminar_tramos":
            ok, err = op_eliminar_tramos(ruta_actual, ruta_sig, p["tramos"])
        elif tipo == "insertar_clip":
            ok, err = op_insertar_clip(ruta_actual, ruta_sig, p["clip"], p["en_segundo"])
        elif tipo == "audio":
            ok, err = op_audio(ruta_actual, ruta_sig, **p)
        elif tipo == "imagen":
            ok, err = op_imagen(ruta_actual, ruta_sig, **p)
        elif tipo == "texto":
            ok, err = op_texto(ruta_actual, ruta_sig, **p)
        elif tipo == "exportar":
            ext = "mp3" if p.get("solo_audio") else "mp4"
            ruta_sig = str(d / f"pipe_{i:02d}.{ext}")
            ok, err = op_exportar(ruta_actual, ruta_sig, p["resolucion"], p.get("solo_audio", False))
        else:
            continue

        if not ok:
            return False, f"Error en paso {i+1} ({tipo}): {err}", ""
        ruta_actual = ruta_sig

    return True, "", ruta_actual


# ─────────────────────────────────────────────────────────────────────────────
# UI principal
# ─────────────────────────────────────────────────────────────────────────────

def mostrar_video_editor():
    st.title("🎬 Editor de Video")
    st.markdown(
        "Configurá todas las ediciones que querés aplicar y procesalas de una sola vez. "
        "Al final descargás el video terminado o lo enviás directo al módulo de subida."
    )

    if not verificar_ffmpeg():
        st.error("❌ FFmpeg no está instalado.")
        st.code("brew install ffmpeg", language="bash")
        return

    # ── 1. Subida ────────────────────────────────────────────────────────────
    st.subheader("1. Subí el video a editar")
    archivo = st.file_uploader(
        "Seleccioná el video",
        type=["mp4", "mov", "avi", "mkv", "webm"],
        key="ve_upload"
    )

    if not archivo:
        st.info("Subí un video para ver las opciones de edición.")
        return

    if st.session_state.get("ve_nombre") != archivo.name:
        ruta = guardar_upload(archivo)
        st.session_state["ve_ruta"] = ruta
        st.session_state["ve_nombre"] = archivo.name

    ruta_video = st.session_state["ve_ruta"]
    info = obtener_info_video(ruta_video)
    dur = info["duracion"]

    if dur == 0:
        st.error("❌ No se pudo leer el video. Probá con otro formato.")
        return

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Duración", segundos_a_tiempo(dur))
    col2.metric("Resolución", f"{info['ancho']}×{info['alto']}")
    col3.metric("FPS", info["fps"])
    col4.metric("Audio", "Sí" if info["tiene_audio"] else "No")

    st.video(archivo)
    st.markdown("---")

    # ── Panel de ediciones ───────────────────────────────────────────────────
    st.subheader("2. Configurá las ediciones")
    st.info(
        "Activá las ediciones que querás con el checkbox de cada sección. "
        "Se aplican **en el orden en que aparecen** (de arriba hacia abajo). "
        "Al terminar, apretá **Procesar todo** al final de la página."
    )

    pasos_activos = []  # Se va llenando con cada sección activa

    # ── A. Recortar ──────────────────────────────────────────────────────────
    with st.expander("✂️ Recortar — Quedarse con un fragmento"):
        st.markdown(
            "**¿Para qué sirve?** Elegís qué parte del video querés conservar. "
            "Todo lo que queda fuera del rango se descarta. "
            "Por ejemplo: si querés solo del minuto 1:00 al 2:30, ponés inicio en 60 y fin en 150."
        )
        act_rec = st.checkbox("Activar Recortar", key="act_rec")
        if act_rec:
            col_r1, col_r2 = st.columns(2)
            with col_r1:
                ini_rec = st.slider("Desde (seg)", 0.0, dur - 0.5, 0.0, 0.5, key="rec_ini")
            with col_r2:
                fin_rec = st.slider("Hasta (seg)", 0.5, dur, dur, 0.5, key="rec_fin")

            if ini_rec < fin_rec:
                st.caption(
                    f"Se conserva: {segundos_a_tiempo(ini_rec)} → {segundos_a_tiempo(fin_rec)} "
                    f"({fin_rec - ini_rec:.1f} seg)"
                )
                pasos_activos.append({
                    "tipo": "recortar",
                    "label": f"Recortar {segundos_a_tiempo(ini_rec)} → {segundos_a_tiempo(fin_rec)}",
                    "params": {"inicio": ini_rec, "fin": fin_rec}
                })
            else:
                st.warning("⚠️ El inicio debe ser menor que el fin.")

    # ── B. Eliminar tramos ───────────────────────────────────────────────────
    with st.expander("🗑️ Eliminar tramos — Borrar partes del medio"):
        st.markdown(
            "**¿Para qué sirve?** Eliminás partes específicas y el video queda unido automáticamente. "
            "Por ejemplo: si entre 0:32 y 1:14 hay algo que no querés, lo marcás y desaparece. "
            "Podés agregar varios tramos para eliminar varias partes distintas."
        )
        act_eli = st.checkbox("Activar Eliminar tramos", key="act_eli")
        if act_eli:
            if "ve_tramos" not in st.session_state:
                st.session_state["ve_tramos"] = [{"ini": 0.0, "fin": min(5.0, dur)}]

            tramos = st.session_state["ve_tramos"]
            tramos_validos = []

            for i, t in enumerate(tramos):
                st.markdown(f"**Tramo {i+1}**")
                col_a, col_b, col_c = st.columns([5, 5, 1])
                with col_a:
                    ini = st.slider(f"Inicio tramo {i+1} (seg)", 0.0, dur - 0.5,
                                    float(t["ini"]), 0.5, key=f"eli_ini_{i}")
                with col_b:
                    fin = st.slider(f"Fin tramo {i+1} (seg)", 0.5, dur,
                                    float(t["fin"]), 0.5, key=f"eli_fin_{i}")
                with col_c:
                    st.markdown("<br><br>", unsafe_allow_html=True)
                    if len(tramos) > 1 and st.button("🗑️", key=f"del_t_{i}"):
                        st.session_state["ve_tramos"].pop(i)
                        st.rerun()
                tramos[i] = {"ini": ini, "fin": fin}
                if ini < fin:
                    tramos_validos.append((ini, fin))
                    st.caption(f"Eliminar: {segundos_a_tiempo(ini)} → {segundos_a_tiempo(fin)} ({fin-ini:.1f} seg)")
                else:
                    st.warning(f"⚠️ Tramo {i+1}: inicio debe ser menor que fin.")

            if st.button("➕ Agregar otro tramo", key="add_tramo"):
                st.session_state["ve_tramos"].append({"ini": 0.0, "fin": min(5.0, dur)})
                st.rerun()

            if tramos_validos:
                total_borrar = sum(f - i for i, f in tramos_validos)
                st.caption(
                    f"Total a eliminar: {total_borrar:.1f} seg → "
                    f"Video resultante: ~{max(0, dur - total_borrar):.1f} seg"
                )
                pasos_activos.append({
                    "tipo": "eliminar_tramos",
                    "label": f"Eliminar {len(tramos_validos)} tramo(s)",
                    "params": {"tramos": tramos_validos}
                })

    # ── C. Insertar clip ─────────────────────────────────────────────────────
    with st.expander("➕ Insertar clip — Agregar un video en una posición"):
        st.markdown(
            "**¿Para qué sirve?** Insertás un video externo en un momento específico del video principal. "
            "Por ejemplo: insertar una intro al principio (segundo 0), "
            "una transición en el segundo 30, o un clip al final."
        )
        act_ins = st.checkbox("Activar Insertar clip", key="act_ins")
        if act_ins:
            if "ve_clips_ins" not in st.session_state:
                st.session_state["ve_clips_ins"] = []

            clips_ins = st.session_state["ve_clips_ins"]

            clip_nuevo = st.file_uploader(
                "Subí el clip a insertar",
                type=["mp4", "mov", "avi", "mkv"],
                key="clip_ins_upload"
            )
            en_seg = st.slider(
                "Insertar en el segundo",
                0.0, dur, 0.0, 0.5, key="clip_ins_seg",
                help="0 = al principio del video. Si ponés el valor de la duración total = al final."
            )
            st.caption(
                f"El clip se insertará en: {segundos_a_tiempo(en_seg)} "
                f"(entre el segundo {en_seg:.1f} y {en_seg:.1f} del video principal)"
            )

            if clip_nuevo and st.button("Agregar este clip a la lista", key="btn_add_clip"):
                ruta_clip = guardar_upload(clip_nuevo, f"ins_{len(clips_ins)}_{clip_nuevo.name}")
                clips_ins.append({"ruta": ruta_clip, "nombre": clip_nuevo.name, "en": en_seg})
                st.session_state["ve_clips_ins"] = clips_ins
                st.rerun()

            if clips_ins:
                st.markdown("**Clips a insertar:**")
                for i, c in enumerate(clips_ins):
                    col_ci1, col_ci2 = st.columns([5, 1])
                    with col_ci1:
                        st.caption(f"📹 {c['nombre']} → en {segundos_a_tiempo(c['en'])}")
                    with col_ci2:
                        if st.button("🗑️", key=f"del_clip_{i}"):
                            clips_ins.pop(i)
                            st.session_state["ve_clips_ins"] = clips_ins
                            st.rerun()

                # Ordenar por posición de inserción (de atrás para adelante para no alterar tiempos)
                for c in sorted(clips_ins, key=lambda x: x["en"], reverse=True):
                    pasos_activos.append({
                        "tipo": "insertar_clip",
                        "label": f"Insertar '{c['nombre']}' en {segundos_a_tiempo(c['en'])}",
                        "params": {"clip": c["ruta"], "en_segundo": c["en"]}
                    })

    # ── D. Imagen y efectos ──────────────────────────────────────────────────
    with st.expander("🎨 Imagen y efectos visuales"):
        st.markdown(
            "**¿Qué podés hacer?** Rotar, voltear, cambiar la velocidad, ajustar brillo/contraste/saturación, "
            "poner en blanco y negro, agregar fade in o fade out. "
            "Podés combinar todos los que querás — se aplican juntos en un solo paso."
        )
        act_img = st.checkbox("Activar Imagen y efectos", key="act_img")
        if act_img:
            col_i1, col_i2 = st.columns(2)
            with col_i1:
                rotacion = st.selectbox("Rotación", [0, 90, 180, 270], key="img_rot",
                                        format_func=lambda x: f"{x}°" if x else "Sin rotación")
                flip_h = st.checkbox("Voltear horizontalmente", key="img_fh")
                flip_v = st.checkbox("Voltear verticalmente", key="img_fv")
                velocidad = st.select_slider(
                    "Velocidad", [0.5, 0.75, 1.0, 1.5, 2.0], value=1.0, key="img_vel",
                    format_func=lambda x: f"{x}x {'🐢' if x < 1 else '🐇' if x > 1 else '▶️'}"
                )
            with col_i2:
                bn = st.checkbox("Blanco y negro", key="img_bn")
                brillo = st.slider("Brillo", -0.5, 0.5, 0.0, 0.05, key="img_bri",
                                   help="0 = sin cambio")
                contraste = st.slider("Contraste", 0.5, 2.0, 1.0, 0.05, key="img_con",
                                      help="1.0 = sin cambio")
                saturacion = st.slider("Saturación", 0.0, 3.0, 1.0, 0.1, key="img_sat",
                                       help="1.0 = sin cambio")
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                fade_in = st.slider("Fade in (seg)", 0.0, 5.0, 0.0, 0.5, key="img_fi",
                                    help="Cuántos segundos tarda en aparecer desde negro")
            with col_f2:
                fade_out = st.slider("Fade out (seg)", 0.0, 5.0, 0.0, 0.5, key="img_fo",
                                     help="Cuántos segundos tarda en desvanecerse a negro al final")

            params_img = dict(
                rotacion=rotacion, flip_h=flip_h, flip_v=flip_v,
                velocidad=velocidad, brillo=brillo, contraste=contraste,
                saturacion=saturacion, bn=bn,
                fade_in=fade_in, fade_out=fade_out,
                duracion=dur, tiene_audio=info["tiene_audio"]
            )
            cambios_img = [k for k, v in {
                "rotación": rotacion != 0, "flip H": flip_h, "flip V": flip_v,
                f"velocidad {velocidad}x": velocidad != 1.0,
                "blanco y negro": bn, f"brillo {brillo:+.2f}": brillo != 0,
                f"contraste {contraste:.2f}": contraste != 1.0,
                f"saturación {saturacion:.2f}": saturacion != 1.0,
                f"fade in {fade_in}s": fade_in > 0, f"fade out {fade_out}s": fade_out > 0,
            }.items() if v]
            if cambios_img:
                st.caption("Cambios activos: " + " · ".join(cambios_img))
            pasos_activos.append({
                "tipo": "imagen",
                "label": f"Imagen: {', '.join(cambios_img) if cambios_img else 'sin cambios'}",
                "params": params_img
            })

    # ── E. Audio ─────────────────────────────────────────────────────────────
    with st.expander("🎵 Edición de audio"):
        st.markdown(
            "**Opciones:** quitar el audio, ajustar el volumen, reemplazarlo por un MP3 que subas, "
            "agregar música de fondo encima del audio original, o silenciar un tramo específico. "
            "Solo podés elegir una opción a la vez."
        )
        act_aud = st.checkbox("Activar edición de audio", key="act_aud")
        if act_aud:
            if not info["tiene_audio"]:
                st.warning("⚠️ Este video no tiene audio. Solo podés agregar audio nuevo.")

            op_aud = st.radio(
                "¿Qué hacemos con el audio?",
                ["Quitar audio", "Ajustar volumen", "Reemplazar audio",
                 "Agregar música de fondo", "Silenciar un tramo"],
                key="op_aud"
            )

            params_aud = {}

            if op_aud == "Quitar audio":
                params_aud = {"quitar": True}
                st.caption("El video resultante no tendrá sonido.")

            elif op_aud == "Ajustar volumen":
                vol = st.slider("Volumen (%)", 0, 200, 100, key="aud_vol") / 100
                params_aud = {"volumen": vol}
                st.caption(f"Volumen: {int(vol*100)}%")

            elif op_aud == "Reemplazar audio":
                st.caption("El audio original se elimina y se pone el que subas.")
                mp3 = st.file_uploader("Subí el audio de reemplazo", type=["mp3", "wav", "m4a"], key="aud_rep")
                if mp3:
                    params_aud = {"reemplazar": guardar_upload(mp3, mp3.name)}
                    st.audio(mp3)
                else:
                    st.warning("Subí un archivo de audio para activar esta opción.")

            elif op_aud == "Agregar música de fondo":
                st.caption("El audio original se mezcla con la música que subas.")
                bg = st.file_uploader("Subí la música", type=["mp3", "wav", "m4a"], key="aud_bg")
                if bg:
                    ruta_bg = guardar_upload(bg, bg.name)
                    col_v1, col_v2 = st.columns(2)
                    with col_v1:
                        ov = st.slider("Volumen audio original (%)", 0, 100, 100, key="aud_ov") / 100
                    with col_v2:
                        bv = st.slider("Volumen música de fondo (%)", 0, 100, 30, key="aud_bv") / 100
                    params_aud = {"bg_music": ruta_bg, "bg_vol": bv, "orig_vol": ov}
                else:
                    st.warning("Subí un archivo de música para activar esta opción.")

            elif op_aud == "Silenciar un tramo":
                st.caption("Solo ese tramo queda en silencio. El resto del audio queda igual.")
                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    sil_i = st.slider("Silenciar desde (seg)", 0.0, dur - 0.5, 0.0, 0.5, key="sil_i")
                with col_s2:
                    sil_f = st.slider("Silenciar hasta (seg)", 0.5, dur, min(5.0, dur), 0.5, key="sil_f")
                params_aud = {"sil_ini": sil_i, "sil_fin": sil_f}
                st.caption(f"Silencio: {segundos_a_tiempo(sil_i)} → {segundos_a_tiempo(sil_f)}")

            if params_aud:
                pasos_activos.append({
                    "tipo": "audio",
                    "label": f"Audio: {op_aud}",
                    "params": params_aud
                })

    # ── F. Texto ─────────────────────────────────────────────────────────────
    with st.expander("💬 Agregar texto"):
        st.markdown(
            "**¿Para qué sirve?** Agrega un texto fijo sobre el video en la posición y momento que elijas. "
            "Podés elegir la posición (centro, arriba, abajo), tamaño, color y en qué segundos aparece y desaparece."
        )
        act_txt = st.checkbox("Activar Agregar texto", key="act_txt")
        if act_txt:
            texto = st.text_input("Texto a mostrar", placeholder="Ej: ¡Suscribite!", key="txt_input")
            col_t1, col_t2 = st.columns(2)
            with col_t1:
                tam = st.slider("Tamaño de fuente", 16, 120, 52, key="txt_tam")
                color_hex = st.color_picker("Color", "#FFFFFF", key="txt_col")
                color_ffmpeg = f"0x{color_hex.lstrip('#')}"
            with col_t2:
                pos = st.selectbox("Posición", ["Centro", "Arriba", "Abajo", "Manual"], key="txt_pos")
                t_ini = st.slider("Aparece en (seg)", 0.0, dur - 0.5, 0.0, 0.5, key="txt_ini")
                t_fin = st.slider("Desaparece en (seg)", 0.5, dur, min(5.0, dur), 0.5, key="txt_fin")

            pos_map = {
                "Centro": ("(w-text_w)/2", "(h-text_h)/2"),
                "Arriba": ("(w-text_w)/2", "40"),
                "Abajo": ("(w-text_w)/2", "h-text_h-40"),
            }
            if pos == "Manual":
                col_mx, col_my = st.columns(2)
                with col_mx:
                    px = st.text_input("X (píxeles desde izquierda)", "100", key="txt_px")
                with col_my:
                    py = st.text_input("Y (píxeles desde arriba)", "100", key="txt_py")
            else:
                px, py = pos_map[pos]

            if texto and t_ini < t_fin:
                st.caption(f"Texto: \"{texto}\" — {segundos_a_tiempo(t_ini)} → {segundos_a_tiempo(t_fin)}")
                pasos_activos.append({
                    "tipo": "texto",
                    "label": f"Texto: \"{texto[:20]}...\"" if len(texto) > 20 else f"Texto: \"{texto}\"",
                    "params": dict(texto=texto, pos_x=px, pos_y=py,
                                   tamaño=tam, color=color_ffmpeg,
                                   inicio=t_ini, fin=t_fin)
                })
            elif texto:
                st.warning("⚠️ El segundo de inicio debe ser menor al de fin.")

    # ── G. Exportar ──────────────────────────────────────────────────────────
    with st.expander("🚀 Exportar — Calidad de salida"):
        st.markdown(
            "**¿Para qué sirve?** Re-encoda el video final con la calidad que elijas. "
            "Recomendado si el video va a subirse a YouTube. "
            "Si no activás esto, el video procesado se descarga en el formato original."
        )
        act_exp = st.checkbox("Activar Exportar con calidad específica", key="act_exp")
        if act_exp:
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                res = st.selectbox("Calidad", ["1080p", "720p", "480p"], key="exp_res")
            with col_e2:
                solo_mp3 = st.checkbox("Solo audio (MP3)", key="exp_mp3")
            st.caption(f"Se va a exportar en {'MP3' if solo_mp3 else res}.")
            pasos_activos.append({
                "tipo": "exportar",
                "label": f"Exportar {'MP3' if solo_mp3 else res}",
                "params": {"resolucion": res, "solo_audio": solo_mp3}
            })

    st.markdown("---")

    # ── Subtítulos (separado del pipeline por el tiempo que tarda) ───────────
    with st.expander("📝 Subtítulos automáticos con IA (Whisper)"):
        st.markdown(
            "**¿Qué hace?** Usa Whisper (IA de OpenAI) para transcribir el audio y genera subtítulos en español. "
            "Se mantiene separado del pipeline porque puede tardar varios minutos. "
            "Procesá primero el resto de las ediciones, descargá el resultado, volvé a subirlo y generá los subtítulos."
        )
        try:
            import whisper
            st.success("✅ Whisper está instalado.")
            if info["tiene_audio"]:
                if st.button("⚙️ Generar subtítulos sobre este video", key="btn_subs"):
                    d = tmp_dir()
                    srt_path = str(d / "subtitulos.srt")
                    vid_path = str(d / "video_subtitulado.mp4")
                    with st.spinner("Cargando modelo Whisper small..."):
                        model = whisper.load_model("small")
                    with st.spinner("Transcribiendo audio (puede tardar varios minutos)..."):
                        result = model.transcribe(ruta_video, language="es")

                    def fmt(s):
                        h, m = int(s)//3600, (int(s)%3600)//60
                        sec, ms = int(s)%60, int((s%1)*1000)
                        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

                    srt = ""
                    for i, seg in enumerate(result["segments"], 1):
                        srt += f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text'].strip()}\n\n"
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(srt)

                    srt_esc = srt_path.replace(":", "\\:").replace("'", "\\'")
                    ok, err = run_ffmpeg(["ffmpeg", "-i", ruta_video, "-vf",
                                          f"subtitles='{srt_esc}'", "-c:a", "copy", "-y", vid_path])
                    if ok:
                        st.success("✅ Subtítulos generados.")
                        col_s1, col_s2 = st.columns(2)
                        with col_s1:
                            with open(vid_path, "rb") as f:
                                st.download_button("⬇️ Video con subtítulos", f.read(),
                                                   "video_subtitulado.mp4", "video/mp4",
                                                   use_container_width=True, type="primary")
                        with col_s2:
                            with open(srt_path, "rb") as f:
                                st.download_button("⬇️ Archivo .srt", f.read(),
                                                   "subtitulos.srt", "text/plain",
                                                   use_container_width=True)
                    else:
                        st.error(f"❌ Error al quemar subtítulos: {err}")
            else:
                st.warning("⚠️ Este video no tiene audio.")
        except ImportError:
            st.error("❌ Whisper no está instalado.")
            st.code("pip install openai-whisper", language="bash")

    st.markdown("---")

    # ── Resumen del pipeline ─────────────────────────────────────────────────
    st.subheader("3. Resumen y procesamiento")

    # Filtrar pasos de imagen si no tienen cambios reales
    pasos_finales = [p for p in pasos_activos
                     if not (p["tipo"] == "imagen" and
                             p["label"].endswith("sin cambios"))]

    if not pasos_finales:
        st.info("No hay ediciones activas todavía. Activá al menos una sección arriba.")
    else:
        st.markdown("**Ediciones que se van a aplicar en este orden:**")
        for i, p in enumerate(pasos_finales, 1):
            st.markdown(f"{i}. {p['label']}")

        st.markdown("")
        if st.button("⚙️ Procesar todo", type="primary", use_container_width=True, key="btn_pipeline"):
            barra = st.progress(0, text="Iniciando...")
            total = len(pasos_finales)

            d = tmp_dir()
            ruta_actual = ruta_video

            todo_ok = True
            for i, paso in enumerate(pasos_finales):
                barra.progress(int(i / total * 90), text=f"Paso {i+1}/{total}: {paso['label']}...")
                ext = "mp3" if (paso["tipo"] == "exportar" and paso["params"].get("solo_audio")) else "mp4"
                ruta_sig = str(d / f"pipe_{i:02d}.{ext}")
                tipo = paso["tipo"]
                p = paso.get("params", {})

                if tipo == "recortar":
                    ok, err = op_recortar(ruta_actual, ruta_sig, p["inicio"], p["fin"])
                elif tipo == "eliminar_tramos":
                    ok, err = op_eliminar_tramos(ruta_actual, ruta_sig, p["tramos"])
                elif tipo == "insertar_clip":
                    ok, err = op_insertar_clip(ruta_actual, ruta_sig, p["clip"], p["en_segundo"])
                elif tipo == "audio":
                    ok, err = op_audio(ruta_actual, ruta_sig, **p)
                elif tipo == "imagen":
                    ok, err = op_imagen(ruta_actual, ruta_sig, **p)
                elif tipo == "texto":
                    ok, err = op_texto(ruta_actual, ruta_sig, **p)
                elif tipo == "exportar":
                    ok, err = op_exportar(ruta_actual, ruta_sig, p["resolucion"], p.get("solo_audio", False))
                else:
                    continue

                if not ok:
                    st.error(f"❌ Error en paso {i+1} ({tipo}): {err}")
                    todo_ok = False
                    break
                ruta_actual = ruta_sig

            if todo_ok:
                barra.progress(100, text="✅ Procesamiento completado.")
                st.success("¡Video procesado correctamente!")

                # Guardar en session_state para el módulo de subida
                st.session_state["video_editado_ruta"] = ruta_actual
                st.session_state["video_editado_listo"] = True

                mime = "audio/mpeg" if ruta_actual.endswith(".mp3") else "video/mp4"
                nombre_dl = "audio_final.mp3" if ruta_actual.endswith(".mp3") else "video_final.mp4"

                with open(ruta_actual, "rb") as f:
                    datos = f.read()

                col_dl1, col_dl2 = st.columns(2)
                with col_dl1:
                    st.download_button(
                        "⬇️ Descargar video final",
                        datos, nombre_dl, mime,
                        use_container_width=True, type="primary"
                    )
                with col_dl2:
                    if st.button("🚀 Enviar al módulo de Subida a YouTube",
                                 use_container_width=True):
                        st.session_state.modulo_activo = "Subida a YouTube"
                        st.rerun()
