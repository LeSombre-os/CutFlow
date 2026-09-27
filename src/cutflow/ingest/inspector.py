from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StreamInfo:
    index: int
    codec_name: str
    codec_type: str
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration: float | None = None
    bit_rate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    language: str | None = None


@dataclass
class MediaInfo:
    filepath: str
    format_name: str
    duration: float | None
    bit_rate: int | None
    size_bytes: int | None
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def video_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "video"]

    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def has_video(self) -> bool:
        return len(self.video_streams) > 0

    @property
    def has_audio(self) -> bool:
        return len(self.audio_streams) > 0

    @property
    def resolution(self) -> tuple[int, int] | None:
        for s in self.video_streams:
            if s.width and s.height:
                return (s.width, s.height)
        return None

    @property
    def fps(self) -> float | None:
        for s in self.video_streams:
            if s.fps:
                return s.fps
        return None

    def to_dict(self) -> dict:
        return {
            "filepath": self.filepath,
            "format_name": self.format_name,
            "duration": self.duration,
            "bit_rate": self.bit_rate,
            "size_bytes": self.size_bytes,
            "resolution": f"{self.resolution[0]}x{self.resolution[1]}" if self.resolution else None,
            "fps": self.fps,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "streams": [
                {
                    "index": s.index,
                    "codec_name": s.codec_name,
                    "codec_type": s.codec_type,
                    "width": s.width,
                    "height": s.height,
                    "fps": s.fps,
                    "duration": s.duration,
                    "bit_rate": s.bit_rate,
                    "sample_rate": s.sample_rate,
                    "channels": s.channels,
                    "language": s.language,
                }
                for s in self.streams
            ],
        }


def _parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        if "/" in value:
            num, den = value.split("/")
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def _run_ffprobe(filepath: str | Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise FileNotFoundError("ffprobe not found — install FFmpeg (https://ffmpeg.org)")
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(filepath),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


class MediaInspector:
    def inspect(self, filepath: str | Path) -> MediaInfo:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {path}")
        data = _run_ffprobe(path)
        fmt = data.get("format", {})
        streams_raw = data.get("streams", [])

        duration: float | None = None
        try:
            if fmt.get("duration"):
                duration = float(fmt["duration"])
        except (ValueError, TypeError):
            pass

        bit_rate: int | None = None
        try:
            if fmt.get("bit_rate"):
                bit_rate = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass

        size_bytes: int | None = None
        try:
            if fmt.get("size"):
                size_bytes = int(fmt["size"])
        except (ValueError, TypeError):
            pass

        streams: list[StreamInfo] = []
        for s in streams_raw:
            fps = _parse_fps(s.get("avg_frame_rate")) or _parse_fps(s.get("r_frame_rate"))
            dur: float | None = None
            try:
                if s.get("duration"):
                    dur = float(s["duration"])
                elif duration is not None and s.get("codec_type") == "video":
                    dur = duration
            except (ValueError, TypeError):
                pass
            br: int | None = None
            try:
                if s.get("bit_rate"):
                    br = int(s["bit_rate"])
            except (ValueError, TypeError):
                pass
            streams.append(StreamInfo(
                index=int(s.get("index", 0)),
                codec_name=s.get("codec_name", "unknown"),
                codec_type=s.get("codec_type", "unknown"),
                width=s.get("width"),
                height=s.get("height"),
                fps=fps,
                duration=dur,
                bit_rate=br,
                sample_rate=int(s["sample_rate"]) if s.get("sample_rate") else None,
                channels=int(s["channels"]) if s.get("channels") else None,
                language=s.get("tags", {}).get("language"),
            ))

        return MediaInfo(
            filepath=str(path.resolve()),
            format_name=fmt.get("format_name", "unknown"),
            duration=duration,
            bit_rate=bit_rate,
            size_bytes=size_bytes,
            streams=streams,
        )

    def inspect_dict(self, filepath: str | Path) -> dict:
        return self.inspect(filepath).to_dict()
