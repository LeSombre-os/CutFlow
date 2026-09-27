from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scene:
    index: int
    start: float
    end: float
    duration: float

    def to_dict(self) -> dict:
        return {"index": self.index, "start": self.start, "end": self.end, "duration": self.duration}


@dataclass
class Keyframe:
    scene_index: int
    timestamp: float
    filepath: str | None = None

    def to_dict(self) -> dict:
        return {"scene_index": self.scene_index, "timestamp": self.timestamp, "filepath": self.filepath}


class SceneDetector:
    def __init__(self, threshold: float = 27.0, min_scene_len: int = 15) -> None:
        self.threshold = threshold
        self.min_scene_len = min_scene_len

    def detect(self, filepath: str | Path) -> list[Scene]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {path}")
        try:
            return self._detect_with_pyscenedetect(path)
        except ImportError:
            return self._detect_with_ffmpeg(path)

    def _detect_with_pyscenedetect(self, path: Path) -> list[Scene]:
        try:
            from scenedetect import open_video, SceneManager
            from scenedetect.detectors import ContentDetector
            from scenedetect.video_splitter import split_video_ffmpeg  # noqa: F401
        except ImportError as e:
            raise ImportError("PySceneDetect not installed: pip install scenedetect[opencv]") from e

        video = open_video(str(path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=self.threshold, min_scene_len=self.min_scene_len))
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list()

        if not scene_list:
            try:
                fps = video.frame_rate or 30.0
                duration = video.duration.get_seconds() if hasattr(video.duration, "get_seconds") else 0
                if duration and duration > 0:
                    return [Scene(index=0, start=0.0, end=duration, duration=duration)]
            except Exception:
                pass
            return [Scene(index=0, start=0.0, end=0.0, duration=0.0)]

        scenes: list[Scene] = []
        for i, (start, end) in enumerate(scene_list):
            s = start.get_seconds()
            e = end.get_seconds()
            scenes.append(Scene(index=i, start=s, end=e, duration=e - s))
        return scenes

    def _detect_with_ffmpeg(self, path: Path) -> list[Scene]:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return []
        cmd = [
            ffmpeg, "-i", str(path),
            "-filter:v", f"select='gt(scene,{self.threshold / 100:.3f})',showinfo",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stderr
        timestamps: list[float] = [0.0]
        for line in output.splitlines():
            if "pts_time:" in line:
                try:
                    part = line.split("pts_time:")[1].strip().split()[0].strip("]")
                    timestamps.append(float(part))
                except (ValueError, IndexError):
                    continue
        try:
            from cutflow.ingest.inspector import MediaInspector
            duration = MediaInspector().inspect(path).duration or 0
        except Exception:
            duration = timestamps[-1] if timestamps else 0
        if duration and timestamps[-1] < duration:
            timestamps.append(duration)
        scenes: list[Scene] = []
        for i in range(len(timestamps) - 1):
            s, e = timestamps[i], timestamps[i + 1]
            if e > s:
                scenes.append(Scene(index=i, start=s, end=e, duration=e - s))
        return scenes if scenes else [Scene(index=0, start=0.0, end=0.0, duration=0.0)]

    def extract_keyframes(
        self,
        filepath: str | Path,
        output_dir: str | Path | None = None,
        scenes: list[Scene] | None = None,
    ) -> list[Keyframe]:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Video file not found: {path}")
        if scenes is None:
            scenes = self.detect(path)
        out = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="cutflow_kf_"))
        out.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return [Keyframe(scene_index=s.index, timestamp=s.start) for s in scenes]

        keyframes: list[Keyframe] = []
        for s in scenes:
            kf_path = out / f"scene_{s.index:04d}_{s.start:.2f}.png"
            cmd = [
                ffmpeg, "-y", "-ss", str(s.start), "-i", str(path),
                "-vframes", "1", "-q:v", "2", str(kf_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and kf_path.exists():
                keyframes.append(Keyframe(scene_index=s.index, timestamp=s.start, filepath=str(kf_path)))
            else:
                keyframes.append(Keyframe(scene_index=s.index, timestamp=s.start, filepath=None))
        return keyframes

    def detect_dict(self, filepath: str | Path) -> list[dict]:
        return [s.to_dict() for s in self.detect(filepath)]
