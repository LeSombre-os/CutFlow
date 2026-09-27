from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cutflow.dsl.models import AudioDucking, AudioMix, CutFlowScript, ProjectSettings
from cutflow.ingest.inspector import MediaInspector
from cutflow.ingest.transcriber import AudioTranscriber, TranscriptionResult
from cutflow.render.subtitles import filter_segments

MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
}


@dataclass
class AutoOptions:
    silence_threshold: float = 0.8
    pad: float = 0.2
    min_keep: float = 0.5
    music: str | None = None
    music_volume: float = 0.5
    ducking_db: float = -12.0
    captions: bool = True
    captions_mode: str = "key"  # off|key|full — sélectivité des sous-titres
    whisper_model: str = "base"


@dataclass
class KeepRange:
    source: str
    start: float
    end: float
    text: str = ""
    pieces: list[tuple[float, float, str]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _merge(ranges: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for s, e in ordered[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def keep_ranges(
    result: TranscriptionResult,
    duration: float | None,
    threshold: float,
    pad: float,
    min_keep: float,
) -> list[KeepRange]:
    """Parole gardée : segments Whisper si dispo, sinon complément des silences FFmpeg."""
    total = duration or (result.duration or 0)
    raw: list[tuple[float, float, str]] = []
    if result.segments:
        for s in result.segments:
            raw.append((max(0, s.start - pad), s.end + pad, s.text))
    elif result.silences and total > 0:
        cursor = 0.0
        for sil in sorted(result.silences, key=lambda x: x.start):
            if sil.start - cursor >= threshold:
                raw.append((cursor, sil.start, ""))
            cursor = max(cursor, sil.end)
        if total - cursor >= threshold:
            raw.append((cursor, total, ""))
    elif total > 0:
        raw.append((0, total, result.text))
    spans = _merge([(s, e) for s, e, _ in raw], gap=threshold / 2)
    out: list[KeepRange] = []
    for s, e in spans:
        s = max(0, s)
        e = min(total, e) if total else e
        if e - s < min_keep:
            continue
        text = " ".join(t for rs, re, t in raw if t and re > s and rs < e).strip()
        pieces = [(rs, re, t) for rs, re, t in raw if t and re > s and rs < e]
        out.append(KeepRange(source="", start=s, end=e, text=text, pieces=pieces))
    return out


@dataclass
class AutoReport:
    script_path: str
    video_path: str | None = None
    files: int = 0
    kept_seconds: float = 0.0
    source_seconds: float = 0.0

    @property
    def kept_ratio(self) -> float:
        return self.kept_seconds / self.source_seconds if self.source_seconds else 0.0

    def to_dict(self) -> dict:
        return {
            "script": self.script_path,
            "video": self.video_path,
            "files": self.files,
            "kept_seconds": round(self.kept_seconds, 2),
            "source_seconds": round(self.source_seconds, 2),
            "kept_ratio": round(self.kept_ratio, 3),
        }


def build_script(
    input_dir: str | Path, opts: AutoOptions | None = None
) -> tuple[CutFlowScript, AutoReport]:
    opts = opts or AutoOptions()
    indir = Path(input_dir)
    files = sorted(p for p in indir.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTS)
    if not files:
        raise FileNotFoundError(f"Aucun rush audio/vidéo dans {indir}")

    inspector = MediaInspector()
    transcriber = AudioTranscriber(model=opts.whisper_model)
    project = ProjectSettings(width=1280, height=720, fps=30.0)
    video_clips: list[dict] = []
    voice_clips: list[dict] = []
    caption_clips: list[dict] = []
    cursor = 0.0
    kept = 0.0
    total_src = 0.0

    for f in files:
        try:
            info = inspector.inspect(f)
        except Exception:
            continue
        dur = info.duration or 0
        total_src += dur
        if info.video_streams and info.resolution and info.fps:
            project.width, project.height = info.resolution
            project.fps = info.fps
        try:
            result = transcriber.transcribe(f, silence_threshold=opts.silence_threshold)
        except Exception:
            result = TranscriptionResult(
                text="", language=None, duration=dur, segments=[], silences=[]
            )
        for kr in keep_ranges(result, dur, opts.silence_threshold, opts.pad, opts.min_keep):
            clip = {
                "source": str(f),
                "source_in": round(kr.start, 3),
                "source_out": round(kr.end, 3),
                "timeline_in": round(cursor, 3),
            }
            if info.has_video:
                video_clips.append(clip)
            if info.has_audio:
                voice_clips.append(dict(clip))
            if opts.captions and opts.captions_mode != "off" and kr.pieces:
                raw_pieces = [
                    {"start": ps, "end": pe, "text": pt} for ps, pe, pt in kr.pieces
                ]
                for fp in filter_segments(raw_pieces, mode=opts.captions_mode):
                    cs = max(fp["start"], kr.start) - kr.start + cursor
                    ce = min(fp["end"], kr.end) - kr.start + cursor
                    if ce - cs < 0.3:
                        continue
                    caption_clips.append({
                        "source": fp["text"].strip(),
                        "timeline_in": round(cs, 3),
                        "duration": round(ce - cs, 3),
                    })
            cursor += kr.duration
            kept += kr.duration

    tracks = []
    if video_clips:
        tracks.append({"id": "v_main", "type": "video", "layer": 0, "clips": video_clips})
    if voice_clips:
        tracks.append({"id": "voice", "type": "audio", "layer": 1, "clips": voice_clips})
    if opts.music:
        tracks.append({"id": "music", "type": "audio", "layer": 0,
                       "volume": opts.music_volume,
                       "clips": [{"source": opts.music, "timeline_in": 0}]})
    if caption_clips:
        tracks.append({"id": "captions", "type": "captions", "layer": 20, "clips": caption_clips})

    audio_mix = None
    if opts.music and voice_clips:
        duck = AudioDucking(
            trigger_track="voice", target_track="music", attenuation_db=opts.ducking_db
        )
        audio_mix = AudioMix(ducking=[duck])
    script = CutFlowScript(project=project, tracks=tracks, audio_mix=audio_mix)
    report = AutoReport(
        script_path="", files=len(files), kept_seconds=kept, source_seconds=total_src
    )
    return script, report


def auto_montage(
    input_dir: str | Path,
    output_video: str | Path | None = None,
    json_out: str | Path | None = None,
    opts: AutoOptions | None = None,
    dry_run: bool = False,
) -> AutoReport:
    from cutflow.render.engine import RenderEngine

    script, report = build_script(input_dir, opts)
    if not script.tracks:
        raise ValueError("Rien à garder : tous les rushs sont silences/vides")
    if json_out is None and output_video is not None:
        json_out = str(Path(output_video).with_suffix(".cutflow.json"))
    if json_out:
        Path(json_out).write_text(script.to_json(), encoding="utf-8")
        report.script_path = str(json_out)
    if output_video and not dry_run:
        RenderEngine(show_progress=False).render_script(script, output_video)
        report.video_path = str(output_video)
    elif output_video:
        print(RenderEngine(show_progress=False).compile_only(script, output_video).to_string())
        report.video_path = str(output_video)
    return report
