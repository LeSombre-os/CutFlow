#!/usr/bin/env python3
"""CutFlow Studio - app web locale (stdlib uniquement).

Lancer avec le venv CutFlow : ~/.venvs/CutFlow/bin/python studio/app.py
"""
import contextlib
import http.server
import itertools
import json
import queue
import shutil
import socket
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

APP_VERSION = "0.2.0"

BASE_DIR = Path(__file__).parent
PROJECTS = BASE_DIR / "projects"
CONFIG_FILE = BASE_DIR / "config.json"
PORT_FILE = BASE_DIR / "port.txt"

PORT = 8001
_JOB_SEQ = itertools.count(1)


def _origin_allowed(origin: str | None) -> bool:
    if not origin or origin == "null":
        return origin != "null"
    try:
        u = urllib.parse.urlparse(origin)
    except Exception:
        return False
    return u.hostname in ("127.0.0.1", "localhost") and u.scheme in ("http", "https")


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _health() -> dict:
    try:
        import faster_whisper  # noqa: F401
        whisper = True
    except Exception:
        whisper = False
    return {
        "ok": True,
        "app": APP_VERSION,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "whisper": whisper,
        "projects": len([p for p in PROJECTS.iterdir() if p.is_dir()]) if PROJECTS.exists() else 0,
    }


class Job:
    """Run pilotable en thread + SSE ré-attachable (pattern youtube-audio-dl)."""

    def __init__(self, kind: str, label: str):
        self.id = f"job-{next(_JOB_SEQ)}"
        self.kind = kind
        self.label = label
        self.state = "running"
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.subs: list[queue.Queue] = []
        self.events: list[dict] = []
        self.result: dict | None = None

    def _emit(self, msg: dict, store=True) -> None:
        msg = {"job": self.id, "kind": self.kind, **msg}
        with self.lock:
            if store:
                self.events.append(msg)
            subs = list(self.subs)
        for q in subs:
            q.put(msg)

    def progress(self, done: int, total: int, info="") -> None:
        self._emit({"state": "running", "done": done, "total": total, "info": info})

    def finish(self, state="done", result=None) -> None:
        with self.lock:
            self.state = state
            self.result = result
        self._emit({"state": state, state: True, "result": result or {}})
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            q.put(None)

    def subscribe(self):
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.subs.append(q)
            past = list(self.events)
        return q, past

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _new_job(kind: str, label: str, target, *args) -> Job:
    job = Job(kind, label)
    with JOBS_LOCK:
        JOBS[job.id] = job

    def _run():
        try:
            target(job, *args)
        except Exception as e:  # noqa: BLE001
            job.finish("failed", {"error": str(e)})

    threading.Thread(target=_run, daemon=True).start()
    return job


def _proj(pid: str) -> Path:
    p = PROJECTS / pid
    if not p.is_dir():
        raise FileNotFoundError(f"Projet inconnu: {pid}")
    return p


def _meta(p: Path) -> dict:
    return json.loads((p / "meta.json").read_text(encoding="utf-8"))


def _analyze_job(job: Job, pid: str) -> None:
    from cutflow.auto.pipeline import MEDIA_EXTS
    from cutflow.ingest.inspector import MediaInspector
    from cutflow.ingest.transcriber import AudioTranscriber

    p = _proj(pid)
    rushes = p / "rushes"
    files = sorted(f for f in rushes.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS)
    inspector, transcriber = MediaInspector(), AudioTranscriber()
    analysis = {"files": [], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    for i, f in enumerate(files):
        if job.cancel.is_set():
            job.finish("cancelled", {"files": len(analysis["files"])})
            return
        job.progress(i, len(files), f.name)
        entry: dict = {"name": f.name, "path": str(f)}
        try:
            info = inspector.inspect(f)
            entry["media"] = info.to_dict()
        except Exception as e:  # noqa: BLE001
            entry["media_error"] = str(e)
        try:
            t = transcriber.transcribe(f)
            entry["transcript"] = t.to_dict()
        except Exception as e:  # noqa: BLE001
            entry["transcript_error"] = str(e)
        analysis["files"].append(entry)
    (p / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    job.finish("done", {"files": len(files)})


def _auto_job(job: Job, pid: str, options: dict) -> None:
    from cutflow.auto.pipeline import AutoOptions, auto_montage

    p = _proj(pid)
    job.progress(0, 2, "plan")
    if job.cancel.is_set():
        job.finish("cancelled")
        return
    out = p / "output.mp4"
    dsl = p / "plan.cutflow.json"
    opts = AutoOptions(
        silence_threshold=float(options.get("silence", 0.8)),
        pad=float(options.get("pad", 0.2)),
        music=options.get("music") or None,
        captions=options.get("captions", True),
        captions_mode=options.get("captions_mode", "key"),
    )
    job.progress(1, 2, "rendu")
    report = auto_montage(p / "rushes", out, dsl, opts, dry_run=False)
    if job.cancel.is_set():
        with contextlib.suppress(OSError):
            out.unlink(missing_ok=True)
        job.finish("cancelled")
        return
    job.finish("done", report.to_dict())


def _parse_multipart(body: bytes, boundary: bytes):
    """Upload minimal : [(filename, bytes)]."""
    parts = []
    for chunk in body.split(b"--" + boundary):
        if b'filename="' not in chunk:
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        line = head.decode("utf-8", "replace")
        name = line.split('filename="')[1].split('"')[0]
        name = Path(name).name
        data = data[: -2] if data.endswith(b"\r\n") else data
        if name and name not in (".", ".."):
            parts.append((name, data))
    return parts


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CutFlowStudio/" + APP_VERSION

    def log_message(self, *a) -> None:
        pass

    def _json(self, obj, status=200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _guard(self) -> bool:
        if not _origin_allowed(self.headers.get("Origin")):
            self._json({"error": "origin refusée"}, 403)
            return False
        return True

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            raw = (BASE_DIR / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/health":
            self._json(_health())
            return
        if path == "/api/projects":
            items = []
            if PROJECTS.exists():
                for p in sorted(PROJECTS.iterdir()):
                    if p.is_dir() and (p / "meta.json").exists():
                        with contextlib.suppress(Exception):
                            items.append({"id": p.name, **_meta(p)})
            self._json({"projects": items})
            return
        parts = path.strip("/").split("/")
        if parts[:2] == ["api", "projects"] and len(parts) == 3:
            try:
                m = _meta(_proj(parts[2]))
            except FileNotFoundError:
                self._json({"error": "projet inconnu"}, 404)
                return
            rushes = sorted(f.name for f in (_proj(parts[2]) / "rushes").iterdir() if f.is_file())
            self._json({"id": parts[2], **m, "files": rushes})
            return
        if parts[:2] == ["api", "projects"] and len(parts) == 4 and parts[3] == "analysis":
            f = _proj(parts[2]) / "analysis.json"
            if not f.exists():
                self._json({"error": "pas d'analyse — lance Analyser"}, 404)
                return
            self._json(json.loads(f.read_text(encoding="utf-8")))
            return
        if parts[:2] == ["api", "projects"] and len(parts) == 4 and parts[3] == "preview":
            self._serve_preview(parts[2])
            return
        if parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[3] == "events":
            self._serve_events(parts[2])
            return
        self._json({"error": "route inconnue"}, 404)

    def do_HEAD(self) -> None:  # noqa: N802
        """Existence + taille sans corps (l'UI teste le preview sans le télécharger)."""
        if not self._guard():
            return
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if parts[:2] == ["api", "projects"] and len(parts) == 4 and parts[3] == "preview":
            try:
                f = _proj(parts[2]) / "output.mp4"
            except FileNotFoundError:
                self.send_response(404)
                self.end_headers()
                return
            if not f.exists():
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def _serve_preview(self, pid: str) -> None:
        try:
            f = _proj(pid) / "output.mp4"
        except FileNotFoundError:
            self._json({"error": "projet inconnu"}, 404)
            return
        if not f.exists():
            self._json({"error": "pas de rendu — lance Montage auto"}, 404)
            return
        size = f.stat().st_size
        start, end = 0, size - 1
        status = 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                status = 206
            except ValueError:
                pass
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with f.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                data = fh.read(min(1024 * 256, left))
                if not data:
                    break
                self.wfile.write(data)
                left -= len(data)

    def _serve_events(self, jid: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(jid)
        if job is None:
            self._json({"error": "job inconnu"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q, past = job.subscribe()
        try:
            for ev in past:
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                if ev is None:
                    break
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            job.unsubscribe(q)

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/projects":
            body = self._body()
            name = (body.get("name") or "Sans titre").strip()[:80]
            rush_dir = (body.get("rush_dir") or "").strip()
            PROJECTS.mkdir(exist_ok=True)
            pid = f"p{int(time.time())}"
            p = PROJECTS / pid
            (p / "rushes").mkdir(parents=True)
            if rush_dir:
                src = Path(rush_dir)
                if src.is_dir():
                    from cutflow.auto.pipeline import MEDIA_EXTS

                    for f in sorted(src.iterdir()):
                        if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
                            try:
                                (p / "rushes" / f.name).symlink_to(f)
                            except Exception:
                                shutil.copy2(f, p / "rushes" / f.name)
            meta = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "rushes": len(list((p / "rushes").iterdir()))}
            (p / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            self._json({"id": pid, **meta})
            return
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "projects":
            pid = parts[2]
            try:
                _proj(pid)
            except FileNotFoundError:
                self._json({"error": "projet inconnu"}, 404)
                return
            action = parts[3] if len(parts) > 3 else ""
            if action == "upload":
                self._handle_upload(pid)
                return
            if action == "analyze":
                job = _new_job("analyze", "Analyse rushes", _analyze_job, pid)
                self._json({"job": job.id})
                return
            if action == "auto":
                job = _new_job("auto", "Montage auto", _auto_job, pid, self._body())
                self._json({"job": job.id})
                return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "cancel":
            with JOBS_LOCK:
                job = JOBS.get(parts[2])
            if job is None:
                self._json({"error": "job inconnu"}, 404)
                return
            job.cancel.set()
            self._json({"ok": True})
            return
        self._json({"error": "route inconnue"}, 404)

    def _handle_upload(self, pid: str) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            self._json({"error": "multipart attendu"}, 400)
            return
        boundary = ctype.split("boundary=")[1].encode()
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"error": "taille invalide"}, 400)
            return
        if n > 8 * 1024**3:
            self._json({"error": "fichier trop gros (8 Go max)"}, 400)
            return
        body = self.rfile.read(n)
        from cutflow.auto.pipeline import MEDIA_EXTS

        saved, rejected = [], []
        for name, data in _parse_multipart(body, boundary):
            if Path(name).suffix.lower() not in MEDIA_EXTS:
                rejected.append(name)
                continue
            dest = _proj(pid) / "rushes" / name
            dest.write_bytes(data)
            saved.append(name)
        self._json({"saved": saved, "rejected": rejected})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._guard():
            return
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
            try:
                p = _proj(parts[2])
            except FileNotFoundError:
                self._json({"error": "projet inconnu"}, 404)
                return
            shutil.rmtree(p)
            self._json({"ok": True})
            return
        self._json({"error": "route inconnue"}, 404)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="CutFlow Studio — montage vidéo local")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    PROJECTS.mkdir(exist_ok=True)
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", args.port))
    except OSError:
        print(f"Port {args.port} occupé — le Studio tourne déjà ?")
        return
    PORT_FILE.write_text(str(args.port), encoding="utf-8")
    url = f"http://127.0.0.1:{args.port}"
    print(f"CutFlow Studio {APP_VERSION} — {url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
