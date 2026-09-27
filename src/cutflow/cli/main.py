from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from cutflow.agent_tools.schemas import TOOL_DEFINITIONS
from cutflow.dsl.validator import get_json_schema, validate_cutflow_file, validate_cutflow_json

app = typer.Typer(name="cutflow", help="CutFlow — montage vidéo programmatif et agentique", rich_markup_mode="rich")
console = Console()
err_console = Console(stderr=True)


@app.command("analyze")
def analyze(
    media_path: Annotated[Path, typer.Argument(help="Chemin vers le fichier média")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Fichier JSON de sortie")] = None,
    model: Annotated[str, typer.Option(help="Modèle Whisper")] = "base",
    transcribe: Annotated[bool, typer.Option("--transcribe/--no-transcribe", help="Activer la transcription")] = False,
    scenes: Annotated[bool, typer.Option("--scenes/--no-scenes", help="Détecter les scènes")] = False,
) -> None:
    """Analyse complète d'un média : métadonnées + transcription + scènes."""
    if not media_path.exists():
        err_console.print(f"[red]Fichier introuvable: {media_path}[/red]")
        raise typer.Exit(1)

    report: dict = {"media": str(media_path)}

    console.print(f"[bold]Analyse de {media_path}...[/bold]")

    try:
        from cutflow.ingest.inspector import MediaInspector
        info = MediaInspector().inspect(media_path)
        report["inspector"] = info.to_dict()
        table = Table(title="Media Info", show_header=True)
        table.add_column("Propriété")
        table.add_column("Valeur")
        table.add_row("Format", info.format_name)
        table.add_row("Durée", f"{info.duration:.2f}s" if info.duration else "N/A")
        table.add_row("Résolution", f"{info.resolution[0]}x{info.resolution[1]}" if info.resolution else "N/A")
        table.add_row("FPS", f"{info.fps:.2f}" if info.fps else "N/A")
        table.add_row("Has Video", str(info.has_video))
        table.add_row("Has Audio", str(info.has_audio))
        for s in info.streams:
            table.add_row(f"Stream #{s.index}", f"{s.codec_type}/{s.codec_name}")
        console.print(table)
    except Exception as e:
        err_console.print(f"[yellow]Inspector échoué: {e}[/yellow]")
        report["inspector_error"] = str(e)

    if transcribe:
        console.print("[dim]Transcription en cours...[/dim]")
        try:
            from cutflow.ingest.transcriber import AudioTranscriber
            result = AudioTranscriber(model=model).transcribe(media_path)
            report["transcription"] = result.to_dict()
            console.print(f"[green]Transcription: {result.text[:200]}[/green]")
            if result.silences:
                console.print(f"[dim]{len(result.silences)} silences détectés[/dim]")
        except Exception as e:
            err_console.print(f"[yellow]Transcription échouée: {e}[/yellow]")
            report["transcription_error"] = str(e)

    if scenes:
        console.print("[dim]Détection de scènes...[/dim]")
        try:
            from cutflow.ingest.scene_detector import SceneDetector
            s_list = SceneDetector().detect(media_path)
            report["scenes"] = [s.to_dict() for s in s_list]
            console.print(f"[green]{len(s_list)} scènes détectées[/green]")
            for s in s_list[:10]:
                console.print(f"  Scene #{s.index}: {s.start:.2f}s -> {s.end:.2f}s ({s.duration:.2f}s)")
        except Exception as e:
            err_console.print(f"[yellow]Scene detection échouée: {e}[/yellow]")
            report["scenes_error"] = str(e)

    if output:
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[bold green]Rapport sauvegardé: {output}[/bold green]")
    else:
        console.print(JSON(json.dumps(report, ensure_ascii=False)))


@app.command("auto")
def auto_cmd(
    input_dir: Annotated[Path, typer.Argument(help="Dossier de rushs audio/vidéo")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Vidéo montée de sortie")] = Path("output/montage.mp4"),  # noqa: B008
    json_out: Annotated[Optional[Path], typer.Option("--json", help="Script DSL JSON généré")] = None,
    music: Annotated[Optional[Path], typer.Option("--music", help="Musique de fond (ducking auto)")] = None,
    silence_threshold: Annotated[float, typer.Option("--silence", help="Durée min d'un silence coupé (s)")] = 0.8,
    pad: Annotated[float, typer.Option("--pad", help="Marge gardée autour de la parole (s)")] = 0.2,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Génère JSON + commande FFmpeg sans rendre")] = False,
    no_captions: Annotated[bool, typer.Option("--no-captions", help="Désactive les sous-titres")] = False,
    captions_mode: Annotated[str, typer.Option("--captions", help="Sous-titres: key ou full")] = "key",
) -> None:
    """Dérushage auto : coupe les silences et assemble les passages parlés."""
    from cutflow.auto.pipeline import AutoOptions, auto_montage

    if not input_dir.is_dir():
        err_console.print(f"[red]Dossier introuvable: {input_dir}[/red]")
        raise typer.Exit(1)
    report = auto_montage(
        input_dir, output, json_out,
        AutoOptions(
            silence_threshold=silence_threshold, pad=pad,
            music=str(music) if music else None,
            captions=not no_captions, captions_mode=captions_mode,
        ),
        dry_run=dry_run,
    )
    console.print(f"[bold green]Montage: {report.kept_seconds:.1f}s gardées / {report.source_seconds:.1f}s ({report.kept_ratio:.0%})[/bold green]")
    if report.script_path:
        console.print(f"[dim]DSL: {report.script_path}[/dim]")


@app.command("render")
def render(
    script: Annotated[Path, typer.Argument(help="Chemin vers le script JSON CutFlow")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Fichier vidéo de sortie")] = Path("output.mp4"),  # noqa: B008
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Affiche la commande FFmpeg sans exécuter")] = False,
) -> None:
    """Rend un script CutFlow JSON en vidéo MP4."""
    if not script.exists():
        err_console.print(f"[red]Script introuvable: {script}[/red]")
        raise typer.Exit(1)

    valid, errors = validate_cutflow_file(script)
    if not valid:
        err_console.print("[red]Script invalide:[/red]")
        for err in errors or []:
            err_console.print(f"  - {err}")
        raise typer.Exit(1)

    console.print(f"[bold]Rendu de {script} -> {output}[/bold]")
    if dry_run:
        from cutflow.dsl.models import CutFlowScript
        from cutflow.render.compiler import FFmpegCompiler
        cs = CutFlowScript.from_file(script)
        compiled = FFmpegCompiler().compile(cs, output)
        console.print(f"[dim]{compiled.to_string()}[/dim]")
        console.print(f"\n[dim]Filter complex:\n{compiled.filter_complex}[/dim]")
        return

    try:
        from cutflow.render.engine import RenderEngine
        engine = RenderEngine()
        result = engine.render_file(script, output)
        console.print(f"[bold green]Rendu terminé: {result}[/bold green]")
    except Exception as e:
        err_console.print(f"[red]Rendu échoué: {e}[/red]")
        raise typer.Exit(1) from e


@app.command("export")
def export_cmd(
    script: Annotated[Path, typer.Argument(help="Chemin vers le script JSON")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Fichier de sortie")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Format: otio ou fcpxml")] = "otio",
) -> None:
    """Exporte un script CutFlow vers OTIO/FCPXML (DaVinci, Premiere, Final Cut)."""
    if not script.exists():
        err_console.print(f"[red]Script introuvable: {script}[/red]")
        raise typer.Exit(1)

    if output is None:
        suffix = ".otio" if fmt == "otio" else ".fcpxml"
        output = script.with_suffix(suffix)

    try:
        from cutflow.dsl.models import CutFlowScript
        from cutflow.export.otio_exporter import OTIOExporter
        cs = CutFlowScript.from_file(script)
        result = OTIOExporter().export(cs, output)
        console.print(f"[bold green]Exporté: {result}[/bold green]")
    except Exception as e:
        err_console.print(f"[red]Export échoué: {e}[/red]")
        raise typer.Exit(1) from e


@app.command("validate")
def validate(
    script: Annotated[Path, typer.Argument(help="Chemin vers le script JSON")],
) -> None:
    """Valide un script CutFlow JSON."""
    if not script.exists():
        err_console.print(f"[red]Fichier introuvable: {script}[/red]")
        raise typer.Exit(1)
    valid, errors = validate_cutflow_file(script)
    if valid:
        console.print("[bold green]Script valide[/bold green]")
    else:
        err_console.print("[red]Script invalide:[/red]")
        for err in errors or []:
            err_console.print(f"  - {err.get('loc')}: {err.get('msg')} ({err.get('type')})")
        raise typer.Exit(1)


@app.command("tools")
def tools_cmd(
    as_json: Annotated[bool, typer.Option("--json", help="Sortie JSON brute")] = False,
) -> None:
    """Affiche les définitions OpenAI Function Calling des tools agentiques."""
    if as_json:
        console.print(JSON(json.dumps(TOOL_DEFINITIONS, indent=2, ensure_ascii=False)))
    else:
        table = Table(title="CutFlow Agent Tools (OpenAI Function Specs)", show_header=True)
        table.add_column("Nom")
        table.add_column("Description")
        for tool in TOOL_DEFINITIONS:
            fn = tool["function"]
            table.add_row(fn["name"], fn["description"][:90])
        console.print(table)
        console.print("\n[dim]Utilisez --json pour le JSON complet (à injecter dans un LLM)[/dim]")


@app.command("schema")
def schema_cmd() -> None:
    """Affiche le JSON Schema du DSL CutFlow."""
    schema = get_json_schema()
    console.print(JSON(json.dumps(schema, indent=2, ensure_ascii=False)))


@app.command("captions")
def captions_cmd(
    transcript: Annotated[Path, typer.Argument(help="Transcription JSON (cutflow analyze --transcribe ou whisper)")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Fichier .ass/.srt de sortie")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Format: ass ou srt")] = "ass",
    mode: Annotated[str, typer.Option("--mode", "-m", help="Sélection: key, full ou off")] = "key",
) -> None:
    """Génère un fichier de sous-titres ASS/SRT depuis une transcription."""
    from cutflow.render.subtitles import (
        filter_segments,
        generate_ass,
        generate_srt,
        segments_from_transcription,
    )

    if not transcript.exists():
        err_console.print(f"[red]Fichier introuvable: {transcript}[/red]")
        raise typer.Exit(1)
    data = json.loads(transcript.read_text(encoding="utf-8"))
    segs = filter_segments(segments_from_transcription(data.get("transcription", data)), mode=mode)
    if not segs:
        err_console.print("[red]Aucun segment dans cette transcription[/red]")
        raise typer.Exit(1)
    if output is None:
        output = transcript.with_suffix(f".{fmt}")
    if fmt == "srt":
        generate_srt(segs, output)
    else:
        generate_ass(segs, output)
    console.print(f"[bold green]Sous-titres: {output} ({len(segs)} segments)[/bold green]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
