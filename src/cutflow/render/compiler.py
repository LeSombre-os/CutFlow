from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cutflow.dsl.models import CutFlowScript, EffectType, TrackType
from cutflow.render.subtitles import generate_ass


@dataclass
class CompiledCommand:
    command: list[str]
    filter_complex: str
    inputs: list[str]
    output: str
    temp_files: list[str] = field(default_factory=list)

    def to_string(self) -> str:
        return shlex.join(self.command)

    def cleanup(self) -> None:
        for f in self.temp_files:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError:
                pass


@dataclass
class _InputOcc:
    source: str
    ss: float | None = None
    t: float | None = None


def _atempo_chain(speed: float) -> list[str]:
    # ponytail: atempo n'accepte que 0.5-100, on chaîne par /2 ou x2
    parts: list[str] = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s}")
    return parts


def _effect_to_filter(effect_type: str, params: dict, intensity: float) -> str | None:
    if effect_type == EffectType.blur.value:
        sigma = params.get("sigma", intensity * 2)
        return f"gblur=sigma={sigma}"
    if effect_type == EffectType.zoom.value:
        # ponytail: zoom statique via scale (pas de zoompan animé), upgrade si besoin
        raw = params.get("to", params.get("zoom", intensity))
        try:
            z = float(raw)
        except (TypeError, ValueError):
            z = float(intensity)
        z = min(max(z, 1.0), 10.0)
        return f"scale=iw*{z}:ih*{z}"
    if effect_type == EffectType.stabilize.value:
        # ponytail: deshake 1-passe, vidstab 2-passes si qualité requise
        return "deshake"
    if effect_type == EffectType.speed.value:
        return None  # géré via setpts/atempo sur le clip
    if effect_type == EffectType.color_grade.value:
        brightness = params.get("brightness", 0)
        contrast = params.get("contrast", 1)
        saturation = params.get("saturation", 1)
        return f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
    if effect_type == EffectType.crop.value:
        w = params.get("w", "in_w")
        h = params.get("h", "in_h")
        x = params.get("x", "(in_w-out_w)/2")
        y = params.get("y", "(in_h-out_h)/2")
        return f"crop={w}:{h}:{x}:{y}"
    return None


def _title_to_filter(params: dict, temp_files: list[str]) -> str | None:
    # ponytail: texte via textfile (pas d'échappement drawtext), DejaVu par défaut
    text = str(params.get("text", "")).strip()
    if not text:
        return None
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
        tmp.write(text)
        temp_files.append(tmp.name)
        textfile = tmp.name
    fontsize = params.get("fontsize", 64)
    color = params.get("color", "white")
    x = params.get("x", "(w-text_w)/2")
    y = params.get("y", "(h-text_h)/2")
    font = params.get("font", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    filt = (
        f"drawtext=textfile='{textfile}':fontfile={font}"
        f":fontsize={fontsize}:fontcolor={color}:x={x}:y={y}"
        ":box=1:boxcolor=black@0.6:boxborderw=24"
    )
    if "start" in params or "end" in params:
        filt += f":enable='between(t,{params.get('start', 0)},{params.get('end', 999999)})'"
    return filt


class FFmpegCompiler:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", ffprobe_bin: str = "ffprobe") -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin

    def compile(self, script: CutFlowScript, output_path: str | Path) -> CompiledCommand:
        output_path = str(output_path)
        # ponytail: 1 occurrence d'input par clip avec -ss/-t (seek direct).
        # Partager un seul -i entre clips mémorisait tout le fichier dans les
        # splits (OOM constaté : 25 Go pour 17 min) — upgrade : concat si besoin.
        occurrences: list[_InputOcc] = []

        def _occ_index(clip) -> int | None:
            if clip.source.startswith("color:"):
                return None
            ss = clip.source_in if clip.source_in > 0 else None
            t = (clip.source_out - clip.source_in) if clip.source_out is not None else None
            occurrences.append(_InputOcc(source=clip.source, ss=ss, t=t))
            return len(occurrences) - 1

        inputs: list[str] = []  # rempli à la fin depuis occurrences
        all_clips: list[tuple[int, str, object, object]] = []

        filter_parts: list[str] = []
        video_labels: list[str] = []
        audio_labels: list[str] = []
        temp_files: list[str] = []
        ass_file: Path | None = None
        base_w, base_h = script.project.width, script.project.height
        fps = script.project.fps

        has_captions = any(t.type == TrackType.captions for t in script.tracks)
        if has_captions:
            for track in script.tracks:
                if track.type == TrackType.captions:
                    segs: list[dict] = []
                    for clip in track.clips:
                        txt = clip.filter_extra or clip.source
                        segs.append({"start": clip.timeline_in, "end": clip.timeline_in + (clip.effective_duration() or 2), "text": txt})
                    if segs:
                        ass_file = generate_ass(segs, width=base_w, height=base_h)
                        temp_files.append(str(ass_file))

        total_duration = script.total_duration()
        if total_duration == 0:
            total_duration = script.project.duration or 10.0

        bg_label = "base"
        filter_parts.append(f"color=c={script.project.background_color}:s={base_w}x{base_h}:r={fps}:d={total_duration:.3f} [{bg_label}]")

        sorted_tracks = sorted(script.tracks, key=lambda t: t.layer)

        current_video = f"[{bg_label}]"
        overlay_count = 0

        for track in sorted_tracks:
            if track.type not in (TrackType.video, TrackType.overlay):
                continue
            for clip in track.clips:
                is_color = clip.source.startswith("color:")
                idx = _occ_index(clip)
                in_label: str
                chain: list[str] = []

                if is_color:
                    color = clip.source.split(":", 1)[1] if ":" in clip.source else "black"
                    dur = clip.effective_duration() or 2.0
                    cl = f"color_{overlay_count}"
                    filter_parts.append(f"color=c={color}:s={base_w}x{base_h}:r={fps}:d={dur:.3f} [{cl}]")
                    in_label = f"[{cl}]"
                else:
                    assert idx is not None
                    occ = occurrences[idx]
                    in_label = f"[{idx}:v]"

                    trim_parts: list[str] = []
                    if occ.ss is not None or occ.t is not None:
                        trim_parts.append("setpts=PTS-STARTPTS")
                    elif clip.source_in > 0 or clip.source_out is not None:
                        start = clip.source_in
                        end = clip.source_out
                        if end is not None:
                            trim_parts.append(f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS")
                        else:
                            trim_parts.append(f"trim=start={start:.3f},setpts=PTS-STARTPTS")
                    if clip.speed != 1.0:
                        trim_parts.append(f"setpts=PTS/{clip.speed}")
                    vf_trim = ",".join(trim_parts) if trim_parts else None
                    if vf_trim:
                        tmp = f"vtrim{overlay_count}"
                        filter_parts.append(f"{in_label} {vf_trim} [{tmp}]")
                        in_label = f"[{tmp}]"

                    scale_filters: list[str] = []
                    if clip.transform:
                        t = clip.transform
                        sx = t.effective_scale_x()
                        sy = t.effective_scale_y()
                        if sx != 1.0 or sy != 1.0:
                            scale_filters.append(f"scale=iw*{sx}:ih*{sy}")
                        if t.rotation != 0:
                            scale_filters.append(f"rotate={t.rotation}*PI/180")
                    for eff in clip.effects:
                        etype = eff.type.value if hasattr(eff.type, "value") else str(eff.type)
                        if etype == EffectType.title.value:
                            continue
                        f = _effect_to_filter(etype, eff.params, eff.intensity)
                        if f:
                            scale_filters.append(f)
                    if scale_filters:
                        tmp2 = f"vfx{overlay_count}"
                        filter_parts.append(f"{in_label} {','.join(scale_filters)} [{tmp2}]")
                        in_label = f"[{tmp2}]"

                for eff in clip.effects:
                    etype = eff.type.value if hasattr(eff.type, "value") else str(eff.type)
                    if etype == EffectType.title.value:
                        tf = _title_to_filter(eff.params, temp_files)
                        if tf:
                            tmpt = f"vtitle{overlay_count}"
                            filter_parts.append(f"{in_label} {tf} [{tmpt}]")
                            in_label = f"[{tmpt}]"

                # ponytail: fades en temps LOCAL du clip (avant décalage).
                # Sur la sortie accumulée, fade-in noircissait tout avant st
                # et fade-out tout après — écran noir global constaté.
                dur = clip.effective_duration()
                for eff in clip.effects:
                    etype_f = eff.type.value if hasattr(eff.type, "value") else str(eff.type)
                    if etype_f != EffectType.fade.value:
                        continue
                    direction = eff.direction.value if hasattr(eff.direction, "value") and eff.direction else "both"
                    fdur = eff.duration or 0.5
                    if dur is None:
                        if direction == "out":
                            continue
                        tmpf = f"vfade{overlay_count}"
                        filter_parts.append(f"{in_label} fade=t=in:st=0:d={min(fdur, 2.0):.3f} [{tmpf}]")
                        in_label = f"[{tmpf}]"
                        continue
                    fd = min(fdur, dur / 2) if dur > 0 else fdur
                    if direction in ("in", "both"):
                        tmpf = f"vfade{overlay_count}"
                        filter_parts.append(f"{in_label} fade=t=in:st=0:d={fd:.3f} [{tmpf}]")
                        in_label = f"[{tmpf}]"
                    if direction in ("out", "both"):
                        tmpf = f"vfade{overlay_count}b"
                        filter_parts.append(
                            f"{in_label} fade=t=out:st={max(0, dur - fd):.3f}:d={fd:.3f} [{tmpf}]"
                        )
                        in_label = f"[{tmpf}]"

                # ponytail: décalage par setpts (transparent avant le début).
                # tpad=start_mode=clone couvrait tout le passé d'une image opaque.
                delay = clip.timeline_in
                if delay > 0:
                    tmp3 = f"vdel{overlay_count}"
                    filter_parts.append(f"{in_label} setpts=PTS-STARTPTS+{delay:.3f}/TB [{tmp3}]")
                    in_label = f"[{tmp3}]"

                x = clip.transform.position_x if clip.transform else 0
                y = clip.transform.position_y if clip.transform else 0
                opacity = clip.transform.opacity if clip.transform and clip.transform.opacity != 1.0 else None

                if opacity is not None and opacity < 1.0:
                    tmp4 = f"vopa{overlay_count}"
                    filter_parts.append(f"{in_label} format=rgba,colorchannelmixer=aa={opacity} [{tmp4}]")
                    in_label = f"[{tmp4}]"

                out_label = f"ov{overlay_count}"
                filter_parts.append(f"{current_video}{in_label} overlay=x={x}:y={y}:eof_action=pass [{out_label}]")

                current_video = f"[{out_label}]"
                overlay_count += 1

        final_video = current_video

        # ponytail: ducking statique (atténuation fixe sur la piste cible),
        # pas de sidechain dynamique — upgrade vers sidechaincompress si besoin.
        ducking_factor: dict[str, float] = {}
        if script.audio_mix and script.audio_mix.ducking:
            for dk in script.audio_mix.ducking:
                ducking_factor[dk.target_track] = 10.0 ** (dk.attenuation_db / 20.0)

        audio_inputs: list[str] = []
        for track in sorted_tracks:
            if track.type != TrackType.audio:
                continue
            if track.muted:
                continue
            for j, clip in enumerate(track.clips):
                idx = _occ_index(clip)
                assert idx is not None
                occ = occurrences[idx]
                alabel = f"[{idx}:a]"
                parts: list[str] = []
                if occ.ss is not None or occ.t is not None:
                    parts.append("asetpts=PTS-STARTPTS")
                elif clip.source_in > 0 or clip.source_out is not None:
                    s = clip.source_in
                    e = clip.source_out
                    if e is not None:
                        parts.append(f"atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS")
                    else:
                        parts.append(f"atrim=start={s:.3f},asetpts=PTS-STARTPTS")
                if clip.speed != 1.0:
                    parts.extend(_atempo_chain(clip.speed))
                vol = clip.volume * track.volume * ducking_factor.get(track.id, 1.0)
                if vol != 1.0:
                    parts.append(f"volume={vol:.4f}")
                delay_ms = int(clip.timeline_in * 1000)
                if delay_ms > 0:
                    parts.append(f"adelay={delay_ms}|{delay_ms}:all=1")
                if parts:
                    tmpa = f"a{len(audio_labels)}"
                    filter_parts.append(f"{alabel} {','.join(parts)} [{tmpa}]")
                    alabel = f"[{tmpa}]"
                audio_labels.append(alabel)

        final_audio: str | None = None
        if audio_labels:
            if len(audio_labels) == 1:
                final_audio = audio_labels[0]
            else:
                joined = "".join(audio_labels)
                filter_parts.append(f"{joined} amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0 [amix]")
                final_audio = "[amix]"

        if script.audio_mix and final_audio:
            if script.audio_mix.master_volume != 1.0:
                tmpm = "amaster"
                mv = script.audio_mix.master_volume
                filter_parts.append(f"{final_audio} volume={mv:.4f} [{tmpm}]")
                final_audio = f"[{tmpm}]"
            if script.audio_mix.normalize:
                # ponytail: loudnorm 1-passe, 2-passes si broadcast requis
                tmpn = "anorm"
                filter_parts.append(f"{final_audio} loudnorm [{tmpn}]")
                final_audio = f"[{tmpn}]"

        if ass_file is not None:
            ass_esc = str(ass_file).replace("\\", "/").replace(":", "\\:")
            tmpv = "vsub"
            filter_parts.append(f"{final_video} ass={ass_esc} [{tmpv}]")
            final_video = f"[{tmpv}]"

        filter_complex = ";\n".join(filter_parts)
        inputs = [o.source for o in occurrences]

        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        for o in occurrences:
            if o.ss is not None:
                cmd.extend(["-ss", f"{o.ss:.3f}"])
            if o.t is not None:
                cmd.extend(["-t", f"{o.t:.3f}"])
            cmd.extend(["-i", o.source])
        if filter_complex:
            cmd.extend(["-filter_complex", filter_complex])
            cmd.extend(["-map", final_video])
            if final_audio:
                cmd.extend(["-map", final_audio])
            else:
                has_audio_input = any(True for _ in inputs)
                if not has_audio_input:
                    cmd.extend(["-an"])
            cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps)])
            if final_audio:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            cmd.append(output_path)
        else:
            cmd.append(output_path)

        return CompiledCommand(
            command=cmd,
            filter_complex=filter_complex,
            inputs=inputs,
            output=output_path,
            temp_files=temp_files,
        )
