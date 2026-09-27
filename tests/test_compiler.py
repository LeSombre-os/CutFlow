import json
import pytest

from cutflow.dsl.models import CutFlowScript
from cutflow.render.compiler import FFmpegCompiler


def _script(data: dict) -> CutFlowScript:
    return CutFlowScript.model_validate(data)


def test_compile_minimal_color():
    s = _script({"project": {"width": 640, "height": 360, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [{"source": "color:red", "timeline_in": 0, "duration": 2}]}
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "color=" in c.filter_complex
    assert "out.mp4" in c.command


def test_compile_with_video_and_audio():
    s = _script({"project": {"width": 1920, "height": 1080, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [{"source": "a.mp4", "source_in": 1, "source_out": 3, "timeline_in": 0}]},
        {"id": "a1", "type": "audio", "layer": 0, "clips": [{"source": "b.mp3", "timeline_in": 0, "volume": 0.5}]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "setpts=PTS-STARTPTS" in c.filter_complex
    assert c.inputs == ["a.mp4", "b.mp3"]
    assert "-ss" in c.command and "-t" in c.command  # seek direct par clip


def test_compile_same_source_twice_seeks():
    s = _script({"project": {"width": 640, "height": 360, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [
            {"source": "a.mp4", "source_in": 0, "source_out": 2, "timeline_in": 0},
            {"source": "a.mp4", "source_in": 10, "source_out": 12, "timeline_in": 2},
        ]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert c.command.count("-i") == 2  # 1 input par clip : pas de split = pas d'OOM
    assert "trim=" not in c.filter_complex


def test_compile_delayed_clip_uses_setpts_not_tpad():
    # tpad=start_mode=clone couvrait tout le passé d'une image opaque (écran noir)
    s = _script({"project": {"width": 640, "height": 360, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [
            {"source": "color:red", "timeline_in": 0, "duration": 2},
            {"source": "color:blue", "timeline_in": 2, "duration": 2},
        ]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "tpad=" not in c.filter_complex
    assert "setpts=PTS-STARTPTS+2.000/TB" in c.filter_complex


def test_compile_fade_is_clip_local():
    # fades globaux noircissaient toute la timeline (in avant st, out après)
    s = _script({"project": {"width": 640, "height": 360, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [
            {"source": "color:red", "timeline_in": 0, "duration": 2,
             "effects": [{"type": "fade", "direction": "both", "duration": 0.5}]},
            {"source": "color:blue", "timeline_in": 2, "duration": 4,
             "effects": [{"type": "fade", "direction": "both", "duration": 0.5}]},
        ]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "fade=t=in:st=0" in c.filter_complex
    assert "fade=t=out:st=1.500" in c.filter_complex
    assert "fade=t=out:st=3.500" in c.filter_complex
    assert "st=2.000" not in c.filter_complex  # aucun fade global


def test_compile_with_effects():
    s = _script({"project": {"width": 1280, "height": 720, "fps": 25}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [{
            "source": "a.mp4", "timeline_in": 0, "duration": 2,
            "effects": [{"type": "blur", "intensity": 2}]
        }]}
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "gblur" in c.filter_complex


def test_compile_with_captions():
    s = _script({"project": {"width": 1920, "height": 1080, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [{"source": "color:black", "timeline_in": 0, "duration": 3}]},
        {"id": "cap", "type": "captions", "layer": 10, "clips": [{"source": "Hello world", "timeline_in": 0, "duration": 2}]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "ass=" in c.filter_complex
    assert len(c.temp_files) == 1


def test_compile_overlay_opacity():
    s = _script({"project": {"width": 1920, "height": 1080, "fps": 30}, "tracks": [
        {"id": "v1", "type": "video", "layer": 0, "clips": [{"source": "color:black", "timeline_in": 0, "duration": 3}]},
        {"id": "ov", "type": "overlay", "layer": 5, "clips": [{"source": "logo.png", "timeline_in": 0, "duration": 3, "transform": {"opacity": 0.5}}]},
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "colorchannelmixer" in c.filter_complex


def test_compile_title_card():
    s = _script({"project": {"width": 1280, "height": 720, "fps": 30}, "tracks": [
        {"id": "t", "type": "video", "layer": 0, "clips": [{
            "source": "color:black", "timeline_in": 0, "duration": 2,
            "effects": [{"type": "title", "params": {"text": "Chapitre 1"}}],
        }]}
    ]})
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert "drawtext=" in c.filter_complex
    assert "textfile=" in c.filter_complex
    assert len(c.temp_files) == 1


def test_compile_sample_project():
    import pathlib
    p = pathlib.Path(__file__).parent.parent / "examples" / "sample_project.json"
    s = CutFlowScript.from_file(p)
    c = FFmpegCompiler().compile(s, "out.mp4")
    assert len(c.filter_complex) > 0
    assert "overlay" in c.filter_complex
