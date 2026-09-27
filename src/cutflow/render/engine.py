from __future__ import annotations

import subprocess
import shutil
import re
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from cutflow.dsl.models import CutFlowScript
from cutflow.render.compiler import FFmpegCompiler, CompiledCommand


class RenderError(RuntimeError):
    pass


class RenderEngine:
    def __init__(self, ffmpeg_bin: str = "ffmpeg", show_progress: bool = True) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.show_progress = show_progress
        self.compiler = FFmpegCompiler(ffmpeg_bin=ffmpeg_bin)

    def _check_ffmpeg(self) -> None:
        if shutil.which(self.ffmpeg_bin) is None:
            raise RenderError(f"FFmpeg not found: '{self.ffmpeg_bin}' — install FFmpeg https://ffmpeg.org")

    def render_script(self, script: CutFlowScript, output_path: str | Path, dry_run: bool = False) -> Path:
        self._check_ffmpeg()
        compiled = self.compiler.compile(script, output_path)
        if dry_run:
            print(compiled.to_string())
            return Path(output_path)
        return self._execute(compiled)

    def render_json(self, json_str: str, output_path: str | Path, dry_run: bool = False) -> Path:
        script = CutFlowScript.model_validate_json(json_str)
        return self.render_script(script, output_path, dry_run=dry_run)

    def render_file(self, script_path: str | Path, output_path: str | Path, dry_run: bool = False) -> Path:
        script = CutFlowScript.from_file(script_path)
        return self.render_script(script, output_path, dry_run=dry_run)

    def compile_only(self, script: CutFlowScript, output_path: str | Path) -> CompiledCommand:
        return self.compiler.compile(script, output_path)

    def _execute(self, compiled: CompiledCommand) -> Path:
        try:
            if self.show_progress:
                return self._execute_with_progress(compiled)
            result = subprocess.run(compiled.command, capture_output=True, text=True, timeout=3600)
            if result.returncode != 0:
                raise RenderError(f"FFmpeg failed (code {result.returncode}):\n{result.stderr[-3000:]}")
            return Path(compiled.output)
        finally:
            compiled.cleanup()

    def _execute_with_progress(self, compiled: CompiledCommand) -> Path:
        process = subprocess.Popen(
            compiled.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        duration_re = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
        time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        total_seconds: float | None = None
        stderr_lines: list[str] = []

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("{task.fields[info]}"),
            ) as progress:
                task = progress.add_task("Rendering", total=100, info="")
                assert process.stderr is not None
                for line in process.stderr:
                    stderr_lines.append(line)
                    if total_seconds is None:
                        m = duration_re.search(line)
                        if m:
                            h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                            total_seconds = h * 3600 + mi * 60 + s
                    m = time_re.search(line)
                    if m and total_seconds and total_seconds > 0:
                        h, mi, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                        cur = h * 3600 + mi * 60 + s
                        pct = min(100, int(cur / total_seconds * 100))
                        progress.update(task, completed=pct, info=f"{cur:.1f}s / {total_seconds:.1f}s")
                process.wait(timeout=3600)
                progress.update(task, completed=100, info="done")
        except subprocess.TimeoutExpired:
            process.kill()
            raise RenderError("FFmpeg timed out after 3600s")

        if process.returncode != 0:
            tail = "".join(stderr_lines[-50:])
            raise RenderError(f"FFmpeg failed (code {process.returncode}):\n{tail[-3000:]}")
        return Path(compiled.output)
