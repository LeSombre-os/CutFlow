from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WordSegment:
    word: str
    start: float
    end: float
    probability: float | None = None


@dataclass
class TranscriptionSegment:
    id: int
    start: float
    end: float
    text: str
    words: list[WordSegment] = field(default_factory=list)
    no_speech_prob: float | None = None


@dataclass
class SilenceSegment:
    start: float
    end: float
    duration: float


@dataclass
class TranscriptionResult:
    text: str
    language: str | None
    duration: float | None
    segments: list[TranscriptionSegment] = field(default_factory=list)
    silences: list[SilenceSegment] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "duration": self.duration,
            "segments": [
                {
                    "id": s.id,
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability} for w in s.words],
                    "no_speech_prob": s.no_speech_prob,
                }
                for s in self.segments
            ],
            "words": [
                {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                for s in self.segments for w in s.words
            ],
            "silences": [{"start": s.start, "end": s.end, "duration": s.duration} for s in self.silences],
        }


def _detect_silences_with_ffmpeg(filepath: str | Path, threshold_db: float = -35, min_duration: float = 0.5) -> list[SilenceSegment]:
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return []
    cmd = [
        ffmpeg, "-i", str(filepath),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stderr
    silences: list[SilenceSegment] = []
    start: float | None = None
    for line in output.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].split("|")[0].strip())
                silences.append(SilenceSegment(start=start, end=end, duration=end - start))
                start = None
            except (ValueError, IndexError):
                pass
    return silences


class AudioTranscriber:
    def __init__(
        self,
        model: str = "base",
        language: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        self.model = model
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model_instance: object | None = None

    def _load_model(self) -> object:
        if self._model_instance is not None:
            return self._model_instance
        try:
            from faster_whisper import WhisperModel
            ct = self.compute_type
            if ct == "auto":
                ct = "int8"
            dev = self.device
            if dev == "auto":
                dev = "cpu"
            self._model_instance = WhisperModel(self.model, device=dev, compute_type=ct)  # type: ignore[call-arg]
            return self._model_instance
        except ImportError:
            pass
        try:
            import whisper  # type: ignore[import-untyped]
            self._model_instance = whisper.load_model(self.model)
            return self._model_instance
        except ImportError as e:
            raise ImportError(
                "No Whisper backend found. Install faster-whisper or openai-whisper: pip install faster-whisper"
            ) from e

    def transcribe(
        self,
        filepath: str | Path,
        silence_threshold: float = 0.8,
        word_timestamps: bool = True,
    ) -> TranscriptionResult:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        try:
            model = self._load_model()
        except ImportError:
            return self._fallback_ffmpeg_silence_only(path, silence_threshold)

        segments: list[TranscriptionSegment] = []
        full_text_parts: list[str] = []
        language: str | None = self.language
        duration: float | None = None

        if hasattr(model, "transcribe") and "faster_whisper" in str(type(model)):
            result_segments, info = model.transcribe(  # type: ignore[attr-defined]
                str(path),
                language=self.language,
                word_timestamps=word_timestamps,
                vad_filter=True,
            )
            language = getattr(info, "language", language)
            duration = getattr(info, "duration", None)
            for seg in result_segments:
                words: list[WordSegment] = []
                if hasattr(seg, "words") and seg.words:
                    for w in seg.words:
                        words.append(WordSegment(word=w.word, start=w.start, end=w.end, probability=getattr(w, "probability", None)))
                segments.append(TranscriptionSegment(
                    id=seg.id, start=seg.start, end=seg.end, text=seg.text.strip(), words=words,
                    no_speech_prob=getattr(seg, "no_speech_prob", None),
                ))
                full_text_parts.append(seg.text.strip())
        else:
            import whisper  # type: ignore[import-untyped]
            result = model.transcribe(str(path), language=self.language, word_timestamps=word_timestamps)  # type: ignore[attr-defined]
            language = result.get("language", language)
            for seg in result.get("segments", []):
                words = []
                for w in seg.get("words", []) or []:
                    words.append(WordSegment(word=w.get("word", ""), start=w.get("start", 0), end=w.get("end", 0)))
                segments.append(TranscriptionSegment(
                    id=seg.get("id", 0), start=seg.get("start", 0), end=seg.get("end", 0),
                    text=seg.get("text", "").strip(), words=words,
                ))
                full_text_parts.append(seg.get("text", "").strip())

        text = " ".join(full_text_parts).strip()
        silences = self._compute_silences(segments, duration, threshold=silence_threshold)

        if not silences:
            silences = _detect_silences_with_ffmpeg(path)

        if duration is None and segments:
            duration = segments[-1].end

        return TranscriptionResult(text=text, language=language, duration=duration, segments=segments, silences=silences)

    def _compute_silences(
        self, segments: list[TranscriptionSegment], duration: float | None, threshold: float
    ) -> list[SilenceSegment]:
        if not segments:
            return []
        silences: list[SilenceSegment] = []
        sorted_segs = sorted(segments, key=lambda s: s.start)
        for i in range(len(sorted_segs) - 1):
            gap = sorted_segs[i + 1].start - sorted_segs[i].end
            if gap >= threshold:
                silences.append(SilenceSegment(start=sorted_segs[i].end, end=sorted_segs[i + 1].start, duration=gap))
        if sorted_segs[0].start >= threshold:
            silences.insert(0, SilenceSegment(start=0, end=sorted_segs[0].start, duration=sorted_segs[0].start))
        if duration is not None and sorted_segs[-1].end < duration:
            tail = duration - sorted_segs[-1].end
            if tail >= threshold:
                silences.append(SilenceSegment(start=sorted_segs[-1].end, end=duration, duration=tail))
        return silences

    def _fallback_ffmpeg_silence_only(self, path: Path, threshold: float) -> TranscriptionResult:
        silences = _detect_silences_with_ffmpeg(path, min_duration=threshold)
        try:
            from cutflow.ingest.inspector import MediaInspector
            info = MediaInspector().inspect(path)
            duration = info.duration
        except Exception:
            duration = None
        return TranscriptionResult(text="", language=None, duration=duration, segments=[], silences=silences)

    def transcribe_dict(self, filepath: str | Path, silence_threshold: float = 0.8) -> dict:
        return self.transcribe(filepath, silence_threshold=silence_threshold).to_dict()
