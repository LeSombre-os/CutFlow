from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "inspect_media",
            "description": "Analyse un fichier média (vidéo/audio) et retourne ses métadonnées : résolution, FPS, durée, codecs, pistes audio/vidéo. Nécessite FFmpeg/ffprobe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Chemin absolu ou relatif vers le fichier média"},
                },
                "required": ["filepath"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_audio",
            "description": "Transcrit l'audio d'une vidéo/audio via Whisper (faster-whisper). Retourne texte, segments avec timestamps mot-à-mot, et silences détectés.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Chemin vers le fichier audio/vidéo"},
                    "model": {"type": "string", "description": "Modèle Whisper (tiny, base, small, medium, large-v3)", "default": "base"},
                    "language": {"type": "string", "description": "Code langue ISO (fr, en...) ou null pour auto-détection"},
                    "silence_threshold": {"type": "number", "description": "Durée minimale d'un silence en secondes", "default": 0.8},
                },
                "required": ["filepath"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_scenes",
            "description": "Détecte les changements de scène dans une vidéo via PySceneDetect. Retourne liste de scènes avec start/end/duration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Chemin vers le fichier vidéo"},
                    "threshold": {"type": "number", "description": "Sensibilité détection (0-100, défaut 27)", "default": 27},
                },
                "required": ["filepath"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_cutflow_json",
            "description": "Valide un JSON CutFlow DSL selon le schéma Pydantic. Retourne valid + erreurs détaillées si invalide.",
            "parameters": {
                "type": "object",
                "properties": {
                    "json_str": {"type": "string", "description": "Contenu JSON du script CutFlow en string"},
                },
                "required": ["json_str"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_cutflow_json",
            "description": "Compile et rend un script CutFlow JSON en vidéo MP4 via FFmpeg. Retourne le chemin du fichier généré.",
            "parameters": {
                "type": "object",
                "properties": {
                    "json_str": {"type": "string", "description": "Script CutFlow en JSON string"},
                    "output_path": {"type": "string", "description": "Chemin de sortie pour la vidéo (ex: /tmp/out.mp4)"},
                    "dry_run": {"type": "boolean", "description": "Si true, génère la commande FFmpeg sans exécuter", "default": False},
                },
                "required": ["json_str", "output_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_to_otio",
            "description": "Exporte un script CutFlow vers OTIO ou FCPXML pour import dans DaVinci Resolve / Premiere Pro / Final Cut.",
            "parameters": {
                "type": "object",
                "properties": {
                    "json_str": {"type": "string", "description": "Script CutFlow en JSON string"},
                    "output_path": {"type": "string", "description": "Chemin de sortie (.otio ou .fcpxml)"},
                },
                "required": ["json_str", "output_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cutflow_schema",
            "description": "Retourne le JSON Schema complet du DSL CutFlow (ProjectSettings, Tracks, Clips, Effects...).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]
