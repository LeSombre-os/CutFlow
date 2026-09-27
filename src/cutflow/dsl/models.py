from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TrackType(str, Enum):
    video = "video"
    audio = "audio"
    captions = "captions"
    overlay = "overlay"


class EffectType(str, Enum):
    zoom = "zoom"
    fade = "fade"
    speed = "speed"
    blur = "blur"
    color_grade = "color_grade"
    crop = "crop"
    stabilize = "stabilize"
    title = "title"


class FadeDirection(str, Enum):
    fade_in = "in"
    fade_out = "out"
    both = "both"


class ProjectSettings(BaseModel):
    width: int = Field(default=1920, ge=16, le=8192)
    height: int = Field(default=1080, ge=16, le=8192)
    fps: float = Field(default=30.0, gt=0, le=240)
    resolution: str | None = Field(default=None, description="Alias '1920x1080', overrides width/height if set")
    output_format: str = Field(default="mp4")
    background_color: str = Field(default="black")
    audio_sample_rate: int = Field(default=48000, ge=8000, le=192000)
    audio_channels: int = Field(default=2, ge=1, le=8)
    duration: float | None = Field(default=None, gt=0, description="Forced timeline duration in seconds")

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^\d+x\d+$", v):
            raise ValueError("resolution must be 'WxH' e.g. '1920x1080'")
        return v

    @model_validator(mode="after")
    def apply_resolution(self) -> ProjectSettings:
        if self.resolution:
            w, h = self.resolution.split("x")
            self.width = int(w)
            self.height = int(h)
        return self

    @property
    def resolution_tuple(self) -> tuple[int, int]:
        return (self.width, self.height)


class Transform(BaseModel):
    scale: float = Field(default=1.0, gt=0, le=10)
    scale_x: float | None = Field(default=None, gt=0, le=10)
    scale_y: float | None = Field(default=None, gt=0, le=10)
    position_x: int = Field(default=0)
    position_y: int = Field(default=0)
    rotation: float = Field(default=0.0, ge=-360, le=360)
    opacity: float = Field(default=1.0, ge=0, le=1)

    def effective_scale_x(self) -> float:
        return self.scale_x if self.scale_x is not None else self.scale

    def effective_scale_y(self) -> float:
        return self.scale_y if self.scale_y is not None else self.scale


class Effect(BaseModel):
    type: EffectType
    intensity: float = Field(default=1.0, ge=0, le=10)
    duration: float | None = Field(default=None, gt=0)
    direction: FadeDirection | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type", mode="before")
    @classmethod
    def coerce_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.lower()
        return v


class Clip(BaseModel):
    source: str = Field(description="Path or URL to media file, or 'color:black' for solid")
    source_in: float = Field(default=0.0, ge=0, description="Trim start in source seconds")
    source_out: float | None = Field(default=None, gt=0, description="Trim end in source seconds")
    timeline_in: float = Field(default=0.0, ge=0, description="Placement on timeline in seconds")
    duration: float | None = Field(default=None, gt=0, description="Explicit timeline duration, overrides source_out-source_in")
    speed: float = Field(default=1.0, gt=0, le=100)
    volume: float = Field(default=1.0, ge=0, le=10)
    transform: Transform | None = None
    effects: list[Effect] = Field(default_factory=list)
    filter_extra: str | None = None

    @model_validator(mode="after")
    def validate_timing(self) -> Clip:
        if self.source_out is not None and self.source_out <= self.source_in:
            raise ValueError("source_out must be > source_in")
        if self.duration is not None and self.timeline_in is not None:
            pass
        return self

    def effective_duration(self) -> float | None:
        if self.duration is not None:
            return self.duration
        if self.source_out is not None:
            raw = self.source_out - self.source_in
            return raw / self.speed if self.speed != 1.0 else raw
        return None

    def timeline_out(self) -> float | None:
        d = self.effective_duration()
        if d is None:
            return None
        return self.timeline_in + d


class Track(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    type: TrackType
    layer: int = Field(default=0, ge=0, le=99)
    clips: list[Clip] = Field(default_factory=list)
    muted: bool = False
    volume: float = Field(default=1.0, ge=0, le=10)
    visible: bool = True

    @field_validator("type", mode="before")
    @classmethod
    def coerce_track_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.lower()
        return v


class AudioDucking(BaseModel):
    trigger_track: str = Field(description="Track id that triggers ducking (e.g. voiceover)")
    target_track: str = Field(description="Track id to attenuate (e.g. music)")
    attenuation_db: float = Field(default=-12.0, le=0, ge=-40)
    fade_duration: float = Field(default=0.3, gt=0, le=5)
    threshold: float = Field(default=-30.0, le=0, ge=-60)
    ratio: float = Field(default=8.0, gt=1, le=20)


class AudioMix(BaseModel):
    ducking: list[AudioDucking] = Field(default_factory=list)
    master_volume: float = Field(default=1.0, ge=0, le=2)
    normalize: bool = False


class CutFlowScript(BaseModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    tracks: list[Track] = Field(default_factory=list)
    audio_mix: AudioMix | None = None
    version: str = Field(default="1.0")

    @field_validator("tracks")
    @classmethod
    def unique_track_ids(cls, v: list[Track]) -> list[Track]:
        ids = [t.id for t in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate track ids: {ids}")
        return v

    @model_validator(mode="after")
    def validate_ducking_refs(self) -> CutFlowScript:
        if self.audio_mix and self.audio_mix.ducking:
            ids = {t.id for t in self.tracks}
            for d in self.audio_mix.ducking:
                if d.trigger_track not in ids:
                    raise ValueError(f"ducking trigger_track '{d.trigger_track}' not found in tracks")
                if d.target_track not in ids:
                    raise ValueError(f"ducking target_track '{d.target_track}' not found in tracks")
        return self

    def total_duration(self) -> float:
        max_out = 0.0
        for track in self.tracks:
            for clip in track.clips:
                out = clip.timeline_out()
                if out is not None and out > max_out:
                    max_out = out
        if self.project.duration is not None:
            return self.project.duration
        return max_out

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent, exclude_none=False)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=False)

    @classmethod
    def from_json(cls, data: str | bytes | dict[str, Any] | Path) -> CutFlowScript:
        if isinstance(data, Path):
            data = data.read_text(encoding="utf-8")
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        if isinstance(data, dict):
            return cls.model_validate(data)
        if isinstance(data, str):
            stripped = data.strip()
            if stripped.startswith("{"):
                return cls.model_validate_json(stripped)
            p = Path(data)
            if p.exists():
                return cls.model_validate_json(p.read_text(encoding="utf-8"))
            return cls.model_validate_json(stripped)
        raise TypeError(f"unsupported input type: {type(data)}")

    @classmethod
    def from_file(cls, path: str | Path) -> CutFlowScript:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    def json_schema(self) -> dict[str, Any]:
        return self.model_json_schema()
