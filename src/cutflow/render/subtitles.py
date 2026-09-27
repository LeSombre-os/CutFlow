from __future__ import annotations

import tempfile
from pathlib import Path


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H88000000,1,0,0,0,100,100,0,0,1,3,1,2,30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

SRT_TIME_FMT = "{h:02d}:{m:02d}:{s:02d},{ms:03d}"
ASS_TIME_FMT = "{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _sec_to_srt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sec_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def generate_srt(segments: list[dict], output_path: str | Path | None = None) -> Path:
    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start = seg.get("start", 0)
        end = seg.get("end", start + 1)
        text = seg.get("text", "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_sec_to_srt(start)} --> {_sec_to_srt(end)}")
        lines.append(text)
        lines.append("")
    content = "\n".join(lines)
    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)
    p = Path(output_path)
    p.write_text(content, encoding="utf-8")
    return p


def generate_ass(
    segments: list[dict],
    output_path: str | Path | None = None,
    width: int = 1920,
    height: int = 1080,
    style: str = "Default",
) -> Path:
    header = ASS_HEADER.format(width=width, height=height)
    events: list[str] = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", start + 1)
        text = seg.get("text", "").strip().replace("\n", "\\N")
        if not text:
            continue
        events.append(f"Dialogue: 0,{_sec_to_ass(start)},{_sec_to_ass(end)},{style},,0,0,0,,{text}")
    content = header + "\n".join(events) + "\n"
    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ass", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        return Path(tmp.name)
    p = Path(output_path)
    p.write_text(content, encoding="utf-8")
    return p


def segments_from_transcription(result: dict) -> list[dict]:
    segs = result.get("segments", [])
    if segs:
        return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segs]
    words = result.get("words", [])
    if words:
        return [{"start": w["start"], "end": w["end"], "text": w["word"]} for w in words]
    return []


FILLERS_FR = {
    "euh", "hum", "hop", "voila", "voilà", "bon", "ben", "bah", "hein", "quoi",
    "du", "coup", "alors", "donc", "voici", "bougez", "pas", "allez", "tiens",
}


def _words(text: str) -> list[str]:
    import re

    return re.findall(r"[a-zàâäéèêëîïôöùûüçœ']+", text.lower())


def is_filler(text: str) -> bool:
    ws = [w for w in _words(text) if w not in FILLERS_FR]
    return len(ws) == 0


def filter_segments(
    segments: list[dict],
    mode: str = "key",
    min_words: int = 4,
    max_cps: float = 20.0,
) -> list[dict]:
    """Sous-titres sélectifs : 'off' = rien, 'full' = tout, 'key' = phrases porteuses."""
    if mode == "off":
        return []
    if mode == "full":
        return segments
    if mode != "key":
        raise ValueError(f"mode inconnu: {mode} (off|full|key)")
    out: list[dict] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text or is_filler(text):
            continue
        meaningful = [w for w in _words(text) if w not in FILLERS_FR]
        if len(meaningful) < min_words:
            continue
        dur = max(0.1, seg.get("end", 0) - seg.get("start", 0))
        if len(text) / dur > max_cps:
            continue
        out.append(seg)
    return out
