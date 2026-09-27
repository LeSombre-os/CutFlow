# CutFlow — Montage vidéo programmatif et agentique

> Définissez votre montage en JSON, laissez l'agent et FFmpeg faire le reste.

CutFlow est un système open-source qui permet de **décrire un montage vidéo de manière déclarative** (DSL JSON/TypeScript), de l'analyser, de le rendre via FFmpeg, et de l'exporter vers les NLE professionnels (DaVinci Resolve, Premiere Pro, Final Cut).

```
  Médias bruts ──► Ingest (Whisper, SceneDetect, ffprobe) ──► DSL JSON ──► FFmpeg / OTIO
                                                              ──► Agent LLM (Tool Calling)
```

## Installation

```bash
pip install -e ".[dev]"
# ou avec uv
uv pip install -e ".[dev]"
```

> **Linux + clé USB exFAT :** `python -m venv .venv` échoue sur exFAT (symlinks interdits).
> Crée le venv sur le disque système : `python3 -m venv ~/.venvs/CutFlow && ~/.venvs/CutFlow/bin/pip install ".[dev]"`.

**Prérequis système :** `ffmpeg` + `ffprobe` dans le PATH ([ffmpeg.org](https://ffmpeg.org))

**Dépendances optionnelles :**
```bash
pip install faster-whisper        # transcription
pip install "scenedetect[opencv]" # détection de scènes
pip install opentimelineio        # export OTIO
```

## Quick Start

```bash
# 1. Analyser un média
cutflow analyze video.mp4 --transcribe --scenes -o report.json

# 2. Valider un script
cutflow validate examples/sample_project.json

# 3. Rendre la vidéo
cutflow render examples/sample_project.json -o output.mp4

# 4. Dry-run (voir la commande FFmpeg)
cutflow render examples/sample_project.json -o output.mp4 --dry-run

# 5. Exporter vers DaVinci / Premiere
cutflow export examples/sample_project.json --format otio -o timeline.otio
cutflow export examples/sample_project.json --format fcpxml -o timeline.fcpxml

# 6. Voir les tools agentiques
cutflow tools --json
cutflow schema
```

## CutFlow Studio (interface web locale)

```bash
./studio/lancer.sh  # http://127.0.0.1:8001 (stdlib uniquement, comme youtube-audio-dl)
# ou : touche Super → cherche “CutFlow” → Entrée (installé dans le menu Applications)
# ou double-clic sur studio/"CutFlow Studio.desktop" (terminal + navigateur)
# (pas sur lancer.sh : Nautilus l'ouvrirait dans l'éditeur)
# Un 2e lancement réutilise le serveur déjà actif au lieu d'en créer un autre.
```

Dépose les rushs (chemin dossier ou upload) → **Analyser** (métadonnées + transcription + silences, progression SSE) → **Montage auto** (seuil silence + sous-titres `key/full/off`) → aperçu `<video>` (seek HTTP Range) + téléchargement. Projets persistés dans `studio/projects/`, annulation de job supportée.

## Dérushage automatique (CLI)

```bash
# Coupe les silences et assemble les passages parlés (Whisper si installé, sinon détection FFmpeg)
cutflow auto assets/rushs -o output/montage.mp4 --music assets/music.mp3
cutflow auto assets/rushs -o output/montage.mp4 --dry-run  # JSON + commande FFmpeg sans rendre
```

> Opencode sait monter avec CutFlow via la skill `cutflow-editor` : analyse → JSON → validate → dry-run → render → vérification.

### Règle audio importante

L'audio d'un clip **vidéo** n'est rendu que si un clip miroir (`source_in/out`, `timeline_in` identiques) existe sur une piste `audio` (la commande `auto` le fait pour toi).

## DSL CutFlow

```json
{
  "project": { "resolution": "1920x1080", "fps": 30, "background_color": "black" },
  "tracks": [
    {
      "id": "main_video", "type": "video", "layer": 0,
      "clips": [
        { "source": "intro.mp4", "source_in": 0, "source_out": 5, "timeline_in": 0,
          "effects": [{ "type": "fade", "direction": "in", "duration": 0.5 }] }
      ]
    },
    {
      "id": "music", "type": "audio", "layer": 0,
      "clips": [{ "source": "music.mp3", "timeline_in": 0, "volume": 0.6 }]
    },
    {
      "id": "voiceover", "type": "audio", "layer": 1,
      "clips": [{ "source": "vo.wav", "timeline_in": 0.5 }]
    }
  ],
  "audio_mix": {
    "ducking": [{ "trigger_track": "voiceover", "target_track": "music", "attenuation_db": -12 }]
  }
}
```

### Types de Tracks

| Type | Description |
|------|-------------|
| `video` | Piste vidéo principale |
| `audio` | Piste audio (mixée via `amix`) |
| `overlay` | Superposition (logo, image) avec transform + opacité |
| `captions` | Sous-titres burn-in (génère un ASS temporaire) |

### Effects

`zoom`, `fade` (in/out/both, temps local du clip), `blur`, `color_grade`, `crop`, `speed`, `stabilize` (deshake), `title` (carton texte drawtext centré, params `text/fontsize/color`)

```json
{ "type": "title", "params": {"text": "Chapitre 1", "fontsize": 56} }
```

### Sous-titres depuis une transcription

```bash
cutflow analyze video.mp4 --transcribe -o report.json
cutflow captions report.json -o subs.ass --mode key  # key (phrases-clés) | full | off
```

Le mode `key` (défaut) retire fillers FR (`euh`, `hop`, `voilà`…), segments < 4 mots porteurs et débits > 20 car/s. Au cas par cas par projet/chapitre dans le Studio.

### Transform

`scale`, `scale_x/y`, `position_x/y`, `rotation`, `opacity`

## Usage programmatique

```python
from cutflow.dsl.models import CutFlowScript
from cutflow.render.engine import RenderEngine
from cutflow.export.otio_exporter import OTIOExporter
from cutflow.ingest.inspector import MediaInspector

# Valider & charger
script = CutFlowScript.from_file("examples/sample_project.json")

# Inspecter un média
info = MediaInspector().inspect("video.mp4")
print(info.to_dict())

# Rendre
engine = RenderEngine()
engine.render_script(script, "output.mp4")

# Exporter OTIO
OTIOExporter().export(script, "timeline.otio")
```

## Agent Tools (LLM Tool Calling)

```python
from cutflow.agent_tools.tools import get_tool_definitions, inspect_media, transcribe_audio

# Définitions OpenAI Function Calling à injecter dans le LLM
tools = get_tool_definitions()

# Appels directs
meta = inspect_media("video.mp4")
transcript = transcribe_audio("video.mp4", model="base", language="fr")
```

CLI équivalent :
```bash
cutflow tools --json  # affiche les 7 définitions OpenAI
```

| Tool | Description |
|------|-------------|
| `inspect_media` | Métadonnées ffprobe |
| `transcribe_audio` | Whisper + silences |
| `detect_scenes` | PySceneDetect |
| `validate_cutflow_json` | Validation Pydantic |
| `render_cutflow_json` | Rendu FFmpeg |
| `export_to_otio` | Export NLE |
| `get_cutflow_schema` | JSON Schema du DSL |

## Architecture

```
src/cutflow/
├── dsl/          # Modèles Pydantic + validateur
├── ingest/       # inspector, transcriber (Whisper), scene_detector
├── render/       # compiler (filter_complex), engine, subtitles (ASS/SRT)
├── export/       # OTIO / FCPXML
├── agent_tools/  # Tools LLM + schemas OpenAI
└── cli/          # Typer CLI (analyze, render, export, validate, tools, schema)
```

## Tests

```bash
pytest -v
pytest --cov=cutflow
```

## Rushs de test (Linux, sans caméra)

`examples/sample_project.json` pointe vers des fichiers fictifs. Utilise `examples/sample_assets.json` + rushs synthétiques générés par FFmpeg :

```bash
mkdir -p assets output
ffmpeg -y -v error -f lavfi -i "testsrc=duration=5:size=1280x720:rate=30" -f lavfi -i "sine=frequency=440:duration=5" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac assets/input1.mp4
ffmpeg -y -v error -f lavfi -i "smptebars=duration=5:size=1280x720:rate=30" -f lavfi -i "sine=frequency=880:duration=5" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac assets/input2.mp4
ffmpeg -y -v error -f lavfi -i "sine=frequency=220:duration=10" -c:a libmp3lame -b:a 128k assets/music.mp3
ffmpeg -y -v error -f lavfi -i "sine=frequency=660:duration=3" -c:a pcm_s16le assets/voiceover.wav
ffmpeg -y -v error -f lavfi -i "color=c=red:s=200x100:d=1:r=30" -vframes 1 assets/logo.png

cutflow validate examples/sample_assets.json
cutflow render examples/sample_assets.json -o output/demo.mp4 --dry-run
cutflow render examples/sample_assets.json -o output/demo.mp4
cutflow export examples/sample_assets.json -f otio -o output/demo.otio
cutflow analyze assets/input1.mp4 -o output/report.json
```

## Licence

AGPL-3.0
