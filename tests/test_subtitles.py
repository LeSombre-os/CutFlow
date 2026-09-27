from cutflow.render.subtitles import filter_segments, generate_ass, generate_srt


def test_generate_srt():
    segs = [{"start": 0, "end": 2, "text": "Hello"}, {"start": 2, "end": 4, "text": "World"}]
    p = generate_srt(segs)
    content = p.read_text(encoding="utf-8")
    assert "Hello" in content
    assert "00:00:00,000" in content
    p.unlink(missing_ok=True)


def test_generate_ass():
    segs = [{"start": 0, "end": 1.5, "text": "Bonjour"}]
    p = generate_ass(segs, width=1920, height=1080)
    content = p.read_text(encoding="utf-8")
    assert "Bonjour" in content
    assert "PlayResX: 1920" in content
    p.unlink(missing_ok=True)


def _seg(text, dur=3.0, start=0.0):
    return {"start": start, "end": start + dur, "text": text}


def test_filter_key_drops_fillers():
    segs = [_seg("Hop voilà euh"), _seg("Le système de report est complet")]
    out = filter_segments(segs, mode="key")
    assert [s["text"] for s in out] == ["Le système de report est complet"]


def test_filter_key_drops_short_and_fast():
    segs = [_seg("Oui"), _seg("a b c d e f g h i j k l m n o p", dur=0.5)]
    assert filter_segments(segs, mode="key") == []


def test_filter_modes_passthrough():
    segs = [_seg("Hop")]
    assert filter_segments(segs, mode="full") == segs
    assert filter_segments(segs, mode="off") == []
