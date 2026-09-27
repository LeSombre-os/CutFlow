from __future__ import annotations

import json
from pathlib import Path

from cutflow.dsl.models import CutFlowScript, TrackType


def _sec_to_rational(t: float, fps: float) -> tuple[float, float]:
    return (t, fps)


class OTIOExporter:
    def __init__(self, fps: float | None = None) -> None:
        self.fps = fps

    def export(self, script: CutFlowScript, output_path: str | Path) -> Path:
        output_path = Path(output_path)
        suffix = output_path.suffix.lower()
        if suffix in (".otio", ".json"):
            return self._export_otio(script, output_path)
        if suffix in (".fcpxml", ".xml"):
            return self._export_fcpxml(script, output_path)
        return self._export_otio(script, output_path)

    def export_json(self, json_str: str, output_path: str | Path) -> Path:
        script = CutFlowScript.model_validate_json(json_str)
        return self.export(script, output_path)

    def _export_otio(self, script: CutFlowScript, output_path: Path) -> Path:
        try:
            import opentimelineio as otio  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError("opentimelineio not installed: pip install opentimelineio") from e

        fps = self.fps or script.project.fps
        timeline = otio.schema.Timeline(name="CutFlow Timeline")
        timeline.global_start_time = otio.opentime.RationalTime(0, fps)

        for track in sorted(script.tracks, key=lambda t: t.layer):
            if track.type == TrackType.captions:
                continue
            kind = otio.schema.TrackKind.Video if track.type in (TrackType.video, TrackType.overlay) else otio.schema.TrackKind.Audio
            otio_track = otio.schema.Track(name=track.id, kind=kind)
            sorted_clips = sorted(track.clips, key=lambda c: c.timeline_in)
            cursor = 0.0
            for clip in sorted_clips:
                if clip.timeline_in > cursor:
                    gap_dur = clip.timeline_in - cursor
                    gap = otio.schema.Gap(
                        source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(0, fps),
                            duration=otio.opentime.RationalTime(gap_dur * fps, fps),
                        )
                    )
                    otio_track.append(gap)
                    cursor = clip.timeline_in

                src = clip.source
                is_color = src.startswith("color:")
                if is_color:
                    generator = otio.schema.GeneratorReference(
                        name=src,
                        generator_kind="SolidColor",
                        parameters={"color": src.split(":", 1)[1] if ":" in src else "black"},
                    )
                    dur = clip.effective_duration() or 1.0
                    otio_clip = otio.schema.Clip(
                        name=src,
                        media_reference=generator,
                        source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(0, fps),
                            duration=otio.opentime.RationalTime(dur * fps, fps),
                        ),
                    )
                else:
                    dur = clip.effective_duration()
                    if dur is None:
                        dur = 2.0
                    mr = otio.schema.ExternalReference(target_url=src)
                    otio_clip = otio.schema.Clip(
                        name=Path(src).name,
                        media_reference=mr,
                        source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(clip.source_in * fps, fps),
                            duration=otio.opentime.RationalTime(dur * fps * clip.speed, fps),
                        ),
                    )
                    if clip.speed != 1.0:
                        effect = otio.schema.LinearTimeWarp(name="Speed", time_scalar=1.0 / clip.speed)
                        otio_clip.effects.append(effect)

                if clip.volume != 1.0 and track.type == TrackType.audio:
                    pass

                otio_track.append(otio_clip)
                cursor = clip.timeline_in + (clip.effective_duration() or 0)

            timeline.tracks.append(otio_track)

        otio.adapters.write_to_file(timeline, str(output_path))
        return output_path

    def _export_fcpxml(self, script: CutFlowScript, output_path: Path) -> Path:
        fps = self.fps or script.project.fps
        w, h = script.project.width, script.project.height

        clips_xml = ""
        for track in sorted(script.tracks, key=lambda t: t.layer):
            if track.type == TrackType.captions:
                continue
            for clip in sorted(track.clips, key=lambda c: c.timeline_in):
                dur_frames = int((clip.effective_duration() or 2.0) * fps)
                offset_frames = int(clip.timeline_in * fps)
                start_frames = int(clip.source_in * fps)
                src = clip.source.replace("&", "&amp;").replace("<", "&lt;")
                if src.startswith("color:"):
                    clips_xml += f'      <gap name="Gap" offset="{offset_frames}/{fps}s" duration="{dur_frames}/{fps}s" start="0s"/>\n'
                else:
                    clips_xml += (
                        f'      <clip name="{Path(src).name}" offset="{offset_frames}/{fps}s" '
                        f'duration="{dur_frames}/{fps}s" start="{start_frames}/{fps}s">\n'
                        f'        <video><media-rep kind="original-media" src="{src}"/></video>\n'
                        f"      </clip>\n"
                    )

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.10">
  <resources>
    <format id="r1" name="FFVideoFormat1080p{fps:.0f}" frameDuration="1/{fps:.0f}s" width="{w}" height="{h}"/>
  </resources>
  <library>
    <event name="CutFlow Event">
      <project name="CutFlow Project">
        <sequence format="r1" duration="{int(script.total_duration() * fps)}/{fps:.0f}s" tcStart="0s">
          <spine>
{clips_xml}          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""
        output_path.write_text(fcpxml, encoding="utf-8")
        return output_path
