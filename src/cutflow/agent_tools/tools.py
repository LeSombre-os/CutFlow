from __future__ import annotations

from pathlib import Path

from cutflow.agent_tools.schemas import TOOL_DEFINITIONS
from cutflow.dsl.validator import get_json_schema, validate_cutflow_json
from cutflow.export.otio_exporter import OTIOExporter
from cutflow.ingest.inspector import MediaInspector
from cutflow.ingest.scene_detector import SceneDetector
from cutflow.ingest.transcriber import AudioTranscriber
from cutflow.render.engine import RenderEngine


def inspect_media(filepath: str) -> dict:
    """Analyse un fichier média et retourne ses métadonnées."""
    return MediaInspector().inspect_dict(filepath)


def transcribe_audio(filepath: str, model: str = "base", language: str | None = None, silence_threshold: float = 0.8) -> dict:
    """Transcrit l'audio via Whisper avec timestamps mot-à-mot et détection de silences."""
    transcriber = AudioTranscriber(model=model, language=language)
    return transcriber.transcribe_dict(filepath, silence_threshold=silence_threshold)


def detect_scenes(filepath: str, threshold: float = 27.0) -> list[dict]:
    """Détecte les scènes d'une vidéo."""
    return SceneDetector(threshold=threshold).detect_dict(filepath)


def validate_cutflow_json_tool(json_str: str) -> dict:
    """Valide un JSON CutFlow. Retourne {'valid': bool, 'errors': [...]}."""
    valid, errors = validate_cutflow_json(json_str)
    return {"valid": valid, "errors": errors}


def render_cutflow_json_tool(json_str: str, output_path: str, dry_run: bool = False) -> str:
    """Rend un script CutFlow JSON en vidéo. Retourne le chemin de sortie."""
    engine = RenderEngine(show_progress=False)
    result = engine.render_json(json_str, output_path, dry_run=dry_run)
    return str(result)


def export_to_otio_tool(json_str: str, output_path: str) -> str:
    """Exporte un script CutFlow vers OTIO/FCPXML."""
    exporter = OTIOExporter()
    result = exporter.export_json(json_str, output_path)
    return str(result)


def get_cutflow_schema() -> dict:
    """Retourne le JSON Schema du DSL CutFlow."""
    return get_json_schema()


def get_tool_definitions() -> list[dict]:
    """Retourne les définitions OpenAI Function Calling de tous les tools."""
    return TOOL_DEFINITIONS


ALL_TOOLS = {
    "inspect_media": inspect_media,
    "transcribe_audio": transcribe_audio,
    "detect_scenes": detect_scenes,
    "validate_cutflow_json": validate_cutflow_json_tool,
    "render_cutflow_json": render_cutflow_json_tool,
    "export_to_otio": export_to_otio_tool,
    "get_cutflow_schema": get_cutflow_schema,
}
