import tempfile
from pathlib import Path

from cutflow.dsl.models import CutFlowScript
from cutflow.export.otio_exporter import OTIOExporter


def test_export_fcpxml():
    s = CutFlowScript.model_validate({
        "project": {"width": 1920, "height": 1080, "fps": 30},
        "tracks": [
            {"id": "v1", "type": "video", "layer": 0, "clips": [
                {"source": "a.mp4", "source_in": 0, "source_out": 2, "timeline_in": 0},
            ]}
        ]
    })
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "test.fcpxml"
        result = OTIOExporter().export(s, out)
        assert result.exists()
        content = result.read_text()
        assert "fcpxml" in content
        assert "a.mp4" in content


def test_export_otio_fallback():
    s = CutFlowScript.model_validate({
        "project": {"width": 1280, "height": 720, "fps": 25},
        "tracks": [
            {"id": "v1", "type": "video", "layer": 0, "clips": [
                {"source": "color:black", "timeline_in": 0, "duration": 2},
            ]}
        ]
    })
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "test.otio"
        try:
            result = OTIOExporter().export(s, out)
            assert result.exists()
        except ImportError:
            pass
