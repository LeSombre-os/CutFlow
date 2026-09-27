import json
import pytest
from pydantic import ValidationError

from cutflow.dsl.models import CutFlowScript, ProjectSettings, Clip, Track, Effect, Transform, AudioDucking
from cutflow.dsl.validator import validate_cutflow_json, validate_cutflow_dict, assert_valid


def test_project_resolution_alias():
    p = ProjectSettings(resolution="1280x720")
    assert p.width == 1280
    assert p.height == 720


def test_project_invalid_resolution():
    with pytest.raises(ValidationError):
        ProjectSettings(resolution="bad")


def test_clip_effective_duration():
    c = Clip(source="a.mp4", source_in=1, source_out=5, timeline_in=0)
    assert c.effective_duration() == 4.0
    c2 = Clip(source="a.mp4", source_in=1, source_out=5, timeline_in=0, speed=2.0)
    assert c2.effective_duration() == 2.0
    c3 = Clip(source="a.mp4", timeline_in=0, duration=3)
    assert c3.effective_duration() == 3.0


def test_clip_invalid_timing():
    with pytest.raises(ValidationError):
        Clip(source="a.mp4", source_in=5, source_out=3, timeline_in=0)


def test_duplicate_track_ids():
    with pytest.raises(ValidationError):
        CutFlowScript(tracks=[
            Track(id="a", type="video", clips=[]),
            Track(id="a", type="audio", clips=[]),
        ])


def test_ducking_ref_validation():
    with pytest.raises(ValidationError):
        CutFlowScript(
            tracks=[Track(id="v1", type="video", clips=[])],
            audio_mix={"ducking": [{"trigger_track": "missing", "target_track": "v1"}]},
        )


def test_roundtrip_serialization():
    script = CutFlowScript(
        project=ProjectSettings(width=1920, height=1080, fps=30),
        tracks=[
            Track(id="v1", type="video", layer=0, clips=[
                Clip(source="test.mp4", source_in=0, source_out=2, timeline_in=0),
            ])
        ],
    )
    j = script.to_json()
    restored = CutFlowScript.model_validate_json(j)
    assert restored.project.width == 1920
    assert len(restored.tracks) == 1


def test_validate_cutflow_json_valid():
    data = {"project": {"width": 1920, "height": 1080, "fps": 30}, "tracks": []}
    valid, errors = validate_cutflow_json(json.dumps(data))
    assert valid is True
    assert errors is None


def test_validate_cutflow_json_invalid():
    valid, errors = validate_cutflow_json("{bad json")
    assert valid is False
    assert errors is not None


def test_validate_cutflow_dict_invalid():
    valid, errors = validate_cutflow_dict({"project": {"fps": -5}})
    assert valid is False


def test_assert_valid_raises():
    with pytest.raises(Exception):
        assert_valid('{"project": {"fps": -1}}')


def test_total_duration():
    script = CutFlowScript(tracks=[
        Track(id="v1", type="video", clips=[
            Clip(source="a.mp4", timeline_in=0, duration=5),
            Clip(source="b.mp4", timeline_in=5, duration=3),
        ])
    ])
    assert script.total_duration() == 8.0


def test_effect_types():
    e = Effect(type="blur", intensity=2.0)
    assert e.type.value == "blur"
    e2 = Effect(type="FADE", intensity=1.0)
    assert e2.type.value == "fade"


def test_transform_defaults():
    t = Transform()
    assert t.scale == 1.0
    assert t.effective_scale_x() == 1.0
    t2 = Transform(scale=2.0, scale_x=0.5)
    assert t2.effective_scale_x() == 0.5


def test_from_json_dict():
    d = {"project": {"width": 1280, "height": 720, "fps": 25}, "tracks": []}
    s = CutFlowScript.from_json(d)
    assert s.project.width == 1280
