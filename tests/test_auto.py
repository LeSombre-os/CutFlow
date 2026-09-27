import shutil
import subprocess

import pytest

from cutflow.auto.pipeline import AutoOptions, build_script, keep_ranges
from cutflow.ingest.transcriber import SilenceSegment, TranscriptionResult, TranscriptionSegment


def _result(segments=(), silences=(), duration=10.0):
    return TranscriptionResult(text="", language=None, duration=duration,
                               segments=list(segments), silences=list(silences))


def test_keep_ranges_from_segments():
    seg = TranscriptionSegment(id=0, start=1.0, end=3.0, text="hello")
    res = _result(segments=[seg], duration=10.0)
    out = keep_ranges(res, 10.0, threshold=0.8, pad=0.2, min_keep=0.5)
    assert len(out) == 1
    assert out[0].start == pytest.approx(0.8) and out[0].end == pytest.approx(3.2)
    assert out[0].text == "hello"


def test_keep_ranges_from_silences():
    r = _result(silences=[SilenceSegment(start=2.0, end=5.0, duration=3.0)], duration=10.0)
    out = keep_ranges(r, 10.0, threshold=0.8, pad=0.0, min_keep=0.5)
    assert [(k.start, k.end) for k in out] == [(0, 2.0), (5.0, 10.0)]


def test_keep_ranges_drops_short_bits():
    seg = TranscriptionSegment(id=0, start=1.0, end=1.2, text="euh")
    res = _result(segments=[seg], duration=10.0)
    out = keep_ranges(res, 10.0, threshold=0.8, pad=0.0, min_keep=0.5)
    assert out == []


def _make_rush(path, bursts=(2.0, 2.0, 2.0), gaps=(2.0, 1.5)):
    parts = []
    for i, d in enumerate(bursts):
        parts += ["-f", "lavfi", "-i", f"sine=frequency={440 + i * 220}:duration={d}"]
        if i < len(gaps):
            parts += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={gaps[i]}"]
    n = len(bursts) + len(gaps)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    cmd = ["ffmpeg", "-y", "-v", "error", *parts,
           "-f", "lavfi", "-i", "testsrc=duration=10:size=640x360:rate=30",
           "-filter_complex", filt, "-map", f"{n}:v", "-map", "[a]",
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True, timeout=120)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis")
def test_build_script_cuts_silences(tmp_path):
    rush = tmp_path / "rush.mp4"
    _make_rush(rush)  # 6s de sine + 3.5s de silence sur ~10s
    opts = AutoOptions(silence_threshold=0.8, pad=0.0, captions=False)
    script, report = build_script(tmp_path, opts)
    assert report.files == 1
    assert report.source_seconds == pytest.approx(10.0, abs=0.5)
    assert report.kept_seconds == pytest.approx(6.0, abs=1.0)
    assert report.kept_ratio < 0.8
    vids = [t for t in script.tracks if t.type == "video"]
    assert len(vids) == 1 and len(vids[0].clips) == 3
