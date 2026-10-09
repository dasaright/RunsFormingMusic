import asyncio
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import queue
import hashlib
import tkinter as tk
from tkinter import filedialog, ttk, simpledialog
from pathlib import Path

import certifi
from websockets.asyncio.client import connect
if __package__:
    from .shared_clips import sync_clips
    from .updater import find_update, prepare_update, launch_installer
else:
    from shared_clips import sync_clips
    from updater import find_update, prepare_update, launch_installer


RELAY_BUILD = 0
RELAY_URL = "wss://runsformingbot-production.up.railway.app/relay"
PROTOCOL_VERSION = 1
PCM_FRAME_BYTES = 3840
MAX_TRACK_SECONDS = 6 * 60 * 60
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma", ".aif", ".aiff"}


def application_directory():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = application_directory()
SHARED_CLIPS_DIR = APP_DIR / "sharedclips"
CONFIG_PATH = APP_DIR / "relay-config.json"
FILES_DIR = APP_DIR / "Files"
YTDLP_PATH = FILES_DIR / "yt-dlp.exe"
FFMPEG_PATH = FILES_DIR / "ffmpeg.exe"


def create_config():
    token = input("Relay token from !token in Discord: ").strip()
    config = {"server_url": RELAY_URL, "relay_token": token, "relay_name": "Discord relay", "cookies_file": ""}
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def load_config():
    if not CONFIG_PATH.is_file():
        return create_config()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["server_url"] = RELAY_URL
    config.setdefault("relay_name", "Discord relay")
    return config


def verify_tools():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    for tool in (YTDLP_PATH, FFMPEG_PATH):
        legacy = APP_DIR / tool.name
        if not tool.exists() and legacy.is_file():
            shutil.move(str(legacy), str(tool))
    missing = [
        path.name for path in (YTDLP_PATH, FFMPEG_PATH) if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "Missing " + ", ".join(missing) + ". Extract the complete relay ZIP."
        )


def youtube_common_args(config):
    args = [
        str(YTDLP_PATH),
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "20",
        "--retries",
        "2",
    ]
    cookies_file = str(config.get("cookies_file") or "").strip()
    if cookies_file:
        cookie_path = Path(cookies_file).expanduser()
        if not cookie_path.is_absolute():
            cookie_path = APP_DIR / cookie_path
        args.extend(("--cookies", str(cookie_path)))
    return args


async def drain_stderr(stream):
    chunks = []
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
        if sum(map(len, chunks)) > 32_000:
            chunks = chunks[-4:]
    return b"".join(chunks).decode("utf-8", errors="replace")[-2000:]


def clean_error(message):
    text = " ".join(str(message).split())
    return text[-500:] if text else "The relay could not process this video."


def relay_ssl_context():
    """Use Mozilla's public roots rather than importing stale Windows roots."""
    return ssl.create_default_context(cafile=certifi.where())


class RelayAgent:
    def __init__(self, config):
        self.config = config
        self.websocket = None
        self.send_lock = asyncio.Lock()
        self.stream_task = None
        self.stream_request_id = None
        self.local_files = {}
        self.clip_tasks = {}
        self.playback_gate = asyncio.Event()
        self.playback_gate.set()
        self.events = queue.Queue()
        self.token_ready = asyncio.Event()
        self.token_ready.set()

    async def send_json(self, payload):
        async with self.send_lock:
            await self.websocket.send(json.dumps(payload))

    async def send_binary(self, payload):
        async with self.send_lock:
            await self.websocket.send(payload)

    async def probe(self, request_id, url, playlist=False):
        args = youtube_common_args(self.config) + [
            "--dump-single-json",
            "--skip-download",
            url,
        ]
        if playlist:
            args.remove("--no-playlist")
            args[1:1] = ["--yes-playlist", "--playlist-end", "50", "--flat-playlist"]
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            stdout, stderr = await process.communicate()
            if process.returncode:
                raise RuntimeError(stderr.decode("utf-8", errors="replace"))
            info = json.loads(stdout.decode("utf-8"))
            if playlist:
                if "entries" not in info:
                    raise ValueError("Provide a YouTube playlist URL.")
                tracks = [{"webpage_url": "https://www.youtube.com/watch?v=" + str(entry["id"]),
                           "title": str(entry.get("title") or "YouTube song")[:200],
                           "duration": int(entry.get("duration") or 0)}
                          for entry in info.get("entries", [])[:50]
                          if entry and entry.get("id") and entry.get("availability") not in
                          {"private", "needs_auth", "subscriber_only", "premium_only"}]
                await self.send_json({"type": "probe_result", "request_id": request_id,
                                      "ok": True, "tracks": tracks})
                return
            duration = int(info.get("duration") or 0)
            if info.get("is_live") or info.get("live_status") in {
                "is_live",
                "is_upcoming",
            }:
                raise ValueError("Live and upcoming streams are not supported.")
            if duration and duration > MAX_TRACK_SECONDS:
                raise ValueError("YouTube videos are limited to six hours.")
            await self.send_json({
                "type": "probe_result",
                "request_id": request_id,
                "ok": True,
                "title": str(info.get("title") or "Untitled YouTube video")[:200],
                "duration": duration,
                "webpage_url": str(info.get("webpage_url") or url),
            })
        except Exception as exc:
            await self.send_json({
                "type": "probe_result",
                "request_id": request_id,
                "ok": False,
                "error": clean_error(exc),
            })

    async def stream(self, request_id, url):
        ytdlp = None
        ffmpeg = None
        pump_task = None
        ytdlp_error_task = None
        ffmpeg_error_task = None
        try:
            local_path = self.local_files.get(url[6:]) if url.startswith("local:") else None
            if url.startswith("local:") and local_path is None:
                raise ValueError("This audio file is no longer in the selected folder. Refresh the library.")
            ytdlp_args = youtube_common_args(self.config) + [
                "--format",
                "bestaudio/best",
                "--output",
                "-",
                url,
            ]
            if local_path is None:
                ytdlp = await asyncio.create_subprocess_exec(
                    *ytdlp_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
            ffmpeg = await asyncio.create_subprocess_exec(
                str(FFMPEG_PATH),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(local_path) if local_path is not None else "pipe:0",
                "-vn",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )

            async def pump_download():
                while True:
                    chunk = await ytdlp.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    ffmpeg.stdin.write(chunk)
                    await ffmpeg.stdin.drain()
                ffmpeg.stdin.close()

            if ytdlp is not None:
                pump_task = asyncio.create_task(pump_download())
                ytdlp_error_task = asyncio.create_task(drain_stderr(ytdlp.stderr))
            ffmpeg_error_task = asyncio.create_task(drain_stderr(ffmpeg.stderr))
            await self.send_json({
                "type": "stream_started",
                "request_id": request_id,
            })
            next_frame_at = asyncio.get_running_loop().time()
            while True:
                try:
                    frame = await ffmpeg.stdout.readexactly(PCM_FRAME_BYTES)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        if local_path is None:
                            await self.playback_gate.wait()
                        frame = exc.partial + bytes(PCM_FRAME_BYTES - len(exc.partial))
                        await self.send_binary(request_id.encode("ascii") + frame)
                    break
                if local_path is None:
                    was_paused = not self.playback_gate.is_set()
                    await self.playback_gate.wait()
                    if was_paused:
                        next_frame_at = asyncio.get_running_loop().time()
                now = asyncio.get_running_loop().time()
                # Absolute deadlines avoid accumulating Windows sleep/send overhead.
                # Bound catch-up after a stalled download so buffers cannot flood.
                next_frame_at = max(next_frame_at, now - 0.1)
                await asyncio.sleep(max(0, next_frame_at - now))
                await self.send_binary(request_id.encode("ascii") + frame)
                next_frame_at += 0.02
            if pump_task is not None:
                await pump_task
            ytdlp_code = await ytdlp.wait() if ytdlp is not None else 0
            ffmpeg_code = await ffmpeg.wait()
            ytdlp_error = await ytdlp_error_task if ytdlp_error_task is not None else ""
            ffmpeg_error = await ffmpeg_error_task
            if ytdlp_code or ffmpeg_code:
                raise RuntimeError(ytdlp_error or ffmpeg_error)
            await self.send_json({
                "type": "stream_end",
                "request_id": request_id,
            })
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self.send_json({
                    "type": "stream_end",
                    "request_id": request_id,
                }))
            except Exception:
                pass
            raise
        except Exception as exc:
            self.events.put({"error": clean_error(exc)})
            await self.send_json({
                "type": "stream_error",
                "request_id": request_id,
                "error": clean_error(exc),
            })
        finally:
            for process in (ffmpeg, ytdlp):
                if process is not None and process.returncode is None:
                    process.kill()
            for task in (pump_task, ytdlp_error_task, ffmpeg_error_task):
                if task is not None and not task.done():
                    task.cancel()
            if url.startswith("local:"):
                self.clip_tasks.pop(request_id, None)
            elif self.stream_request_id == request_id:
                self.stream_task = None
                self.stream_request_id = None

    async def handle_command(self, raw):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        message_type = payload.get("type")
        request_id = str(payload.get("request_id") or "")
        url = str(payload.get("url") or "")
        if message_type == "control_result":
            self.events.put(payload)
        elif message_type == "probe" and request_id and url:
            asyncio.create_task(self.probe(request_id, url, bool(payload.get("playlist"))))
        elif message_type == "pause" and request_id == self.stream_request_id:
            self.playback_gate.clear()
        elif message_type == "resume" and request_id == self.stream_request_id:
            self.playback_gate.set()
        elif message_type == "cancel" and request_id in self.clip_tasks:
            self.clip_tasks[request_id].cancel()
        elif message_type == "stream" and request_id and url:
            if url.startswith("local:"):
                if len(self.clip_tasks) >= 8:
                    await self.send_json({"type": "stream_error", "request_id": request_id, "error": "Too many soundboard clips."})
                else:
                    self.clip_tasks[request_id] = asyncio.create_task(self.stream(request_id, url))
                return
            if self.stream_task is not None:
                await self.send_json({
                    "type": "stream_error",
                    "request_id": request_id,
                    "error": "This relay is already streaming audio.",
                })
                return
            self.playback_gate.set()
            self.stream_request_id = request_id
            self.stream_task = asyncio.create_task(self.stream(request_id, url))
        elif (
            message_type == "cancel"
            and request_id == self.stream_request_id
            and self.stream_task is not None
        ):
            self.stream_task.cancel()

    async def connect_once(self):
        async with connect(
            self.config["server_url"],
            ssl=relay_ssl_context(),
            ping_interval=20,
            ping_timeout=30,
            max_size=1024 * 1024,
        ) as websocket:
            self.websocket = websocket
            await self.send_json({
                "type": "hello",
                "capabilities": ["playlist", "multistream"],
                "protocol": PROTOCOL_VERSION,
                "token": self.config["relay_token"],
                "name": self.config["relay_name"],
            })
            raw_hello = await asyncio.wait_for(websocket.recv(), timeout=15)
            hello = json.loads(raw_hello)
            if hello.get("type") != "hello_ok":
                raise ConnectionError("Relay authentication failed.")
            self.config["relay_name"] = hello.get("relay_name", "Discord relay")
            self.events.put({"relay_name": self.config["relay_name"], "status": "Connected as " + self.config["relay_name"]})
            await self.send_json({"type": "targets"})
            async for message in websocket:
                if isinstance(message, str):
                    await self.handle_command(message)

    async def run(self):
        delay = 2
        while True:
            try:
                await self.connect_once()
                delay = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for task in list(self.clip_tasks.values()):
                    task.cancel()
                await asyncio.gather(*list(self.clip_tasks.values()), return_exceptions=True)
                if self.stream_task is not None:
                    self.stream_task.cancel()
                    await asyncio.gather(self.stream_task, return_exceptions=True)
                self.websocket = None
                self.events.put({"error": f"Disconnected: {clean_error(exc)}"})
                close_frame = getattr(exc, "rcvd", None)
                if close_frame is not None and close_frame.code == 4003:
                    self.token_ready.clear()
                    self.events.put({"token_required": True})
                    await self.token_ready.wait()
                    delay = 2
                    continue
                print(f"Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)


def update_ytdlp():
    try:
        subprocess.run(
            [str(YTDLP_PATH), "--update"],
            check=False,
            timeout=120,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        print("yt-dlp update check failed; continuing with the bundled version.")


def is_playable_audio(file, ffmpeg_path=FFMPEG_PATH):
    file = Path(file)
    try:
        if not file.is_file() or file.suffix.lower() not in AUDIO_EXTENSIONS or file.stat().st_size == 0:
            return False
        result = subprocess.run(
            [str(ffmpeg_path), "-v", "error", "-xerror", "-i", str(file),
             "-t", "0.05", "-map", "0:a:0", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def scan_audio_folder(folder, ffmpeg_path=FFMPEG_PATH):
    """Only indexed, decodable audio files can be requested by the server."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        return {}
    return {hashlib.sha256(str(file.resolve()).encode()).hexdigest(): file.resolve()
            for file in sorted(path.iterdir(), key=lambda file: file.name.casefold())
            if is_playable_audio(file, ffmpeg_path)}


# Keep compatibility with existing imports and relay configuration.
scan_mp3_folder = scan_audio_folder


def copy_soundboard_files(paths, folder, ffmpeg_path=FFMPEG_PATH):
    destination = Path(folder).expanduser()
    if not destination.is_dir():
        raise ValueError("Choose an existing soundboard folder first.")
    copied, skipped = [], []
    for raw in paths:
        source = Path(raw)
        if not is_playable_audio(source, ffmpeg_path):
            skipped.append(source.name)
            continue
        if source.resolve().parent == destination.resolve():
            skipped.append(source.name)
            continue
        candidate = destination / source.name
        number = 2
        while True:
            try:
                # Exclusive creation preserves existing files and concurrent drops.
                with candidate.open("xb") as output:
                    try:
                        with source.open("rb") as input_file:
                            shutil.copyfileobj(input_file, output)
                    except Exception:
                        output.close()
                        candidate.unlink(missing_ok=True)
                        raise
                copied.append(candidate)
                break
            except FileExistsError:
                candidate = destination / f"{source.stem} ({number}){source.suffix}"
                number += 1
            except OSError:
                skipped.append(source.name)
                break
    return copied, skipped


def create_root():
    from tkinterdnd2 import TkinterDnD
    return TkinterDnD.Tk()


class RelayWindow:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.agent = RelayAgent(config)
        self.loop = asyncio.new_event_loop()
        self.targets = []
        self.file_ids = []
        root.title("Runsforming Audio Relay")
        root.geometry("1000x620")
        root.minsize(800, 450)
        self.status = tk.StringVar(value="Connecting…")
        self.folder = tk.StringVar(value=config.get("mp3_folder", ""))
        destination_bar = ttk.Frame(root)
        destination_bar.pack(fill="x", padx=16, pady=12)
        self.destination = ttk.Combobox(destination_bar, state="readonly", width=60)
        self.destination.pack(side="left", fill="x", expand=True)
        self.destination.bind("<<ComboboxSelected>>", lambda event: self.refresh_music())
        ttk.Button(destination_bar, text="Refresh Discord", command=lambda: self.send({"type": "targets"})).pack(side="left", padx=8)
        panes = ttk.Panedwindow(root, orient=tk.HORIZONTAL)
        panes.pack(fill="both", expand=True, padx=16)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        ttk.Label(left, text="YouTube music", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=8)
        music_controls = ttk.Frame(left)
        music_controls.pack(fill="x", pady=8)
        self.previous_button = ttk.Button(music_controls, text="Previous", command=lambda: self.music_control("previous"))
        self.previous_button.pack(side="left")
        self.toggle_button = ttk.Button(music_controls, text="Pause / Play", command=lambda: self.music_control("toggle"))
        self.toggle_button.pack(side="left", padx=8)
        ttk.Button(music_controls, text="Next", command=lambda: self.music_control("next")).pack(side="left")
        self.music_status = tk.StringVar(value="No music playing")
        ttk.Label(left, textvariable=self.music_status, wraplength=440).pack(anchor="w", pady=8)
        self.queue_view = ttk.Treeview(left, columns=("song",), show="headings", selectmode="browse")
        self.queue_view.heading("song", text="Current song and queue")
        self.queue_pressed = None
        self.queue_view.bind("<ButtonPress-1>", self.song_mouse_down)
        self.queue_view.bind("<ButtonRelease-1>", self.song_selected)
        self.queue_view.column("song", width=400)
        self.queue_view.tag_configure("current", background="#dceeff", foreground="#14467a", font=("Segoe UI", 10, "bold"))
        queue_scroll = ttk.Scrollbar(left, orient="vertical", command=self.queue_view.yview)
        self.queue_view.configure(yscrollcommand=queue_scroll.set)
        queue_scroll.pack(side="right", fill="y")
        self.queue_view.pack(fill="both", expand=True, padx=(0, 12))
        ttk.Label(right, text="Soundboard clips", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=8)
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Choose folder", command=self.choose_folder).pack(side="left")
        ttk.Button(buttons, text="Refresh files", command=self.refresh_files).pack(side="left", padx=8)
        ttk.Button(buttons, text="Stop all clips", command=self.stop).pack(side="left")
        ttk.Label(right, textvariable=self.folder, wraplength=440).pack(anchor="w", pady=8)
        ttk.Button(buttons, text="Sync clips", command=self.sync_shared).pack(side="left", padx=8)
        self.sync_running = False
        self.sort_column = "name"
        self.sort_reverse = False
        self.clip_origins = {}
        self.listbox = ttk.Treeview(right, columns=("name", "shared"), show="headings", selectmode="browse")
        self.listbox.heading("name", text="Name", command=lambda: self.sort_clips("name"))
        self.listbox.heading("shared", text="Shared", command=lambda: self.sort_clips("shared"))
        self.listbox.column("name", width=270)
        self.listbox.column("shared", width=80, stretch=False)
        clip_scroll = ttk.Scrollbar(right, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=clip_scroll.set)
        clip_scroll.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True)
        self.clip_pressed_index = None
        self.listbox.bind("<ButtonPress-1>", self.clip_mouse_down)
        self.listbox.bind("<ButtonRelease-1>", self.play_selected)
        ttk.Label(root, textvariable=self.status, wraplength=960).pack(anchor="w", padx=16, pady=8)
        ttk.Label(right, text="Drop audio files anywhere in this window to copy them here.", wraplength=440).pack(anchor="w", pady=6)
        self.register_drop_targets(root)
        update_bar = ttk.Frame(root)
        update_bar.pack(fill="x", padx=16, pady=4)
        self.auto_update = tk.BooleanVar(value=bool(config.get("auto_update", True)))
        ttk.Checkbutton(update_bar, text="Update automatically", variable=self.auto_update,
                        command=self.toggle_auto_update).pack(side="left")
        self.update_button = ttk.Button(update_bar, text="Check for updates",
                                       command=lambda: self.check_updates(manual=True))
        self.update_button.pack(side="left", padx=8)
        self.install_button = ttk.Button(update_bar, text="Install update now", command=self.install_update_now, state="disabled")
        self.install_button.pack(side="left")
        ttk.Label(update_bar, text=f"RunsFormingMusic v1.{RELAY_BUILD}").pack(side="right")
        self.update_check_running = False
        self.pending_update = None
        status_file = FILES_DIR / "update-status.txt"
        failed_update = status_file.is_file() and "failed" in status_file.read_text(encoding="utf-8-sig", errors="replace").lower()
        self.next_update_check = time.monotonic() + 6 * 60 * 60 if failed_update else 0
        if failed_update:
            self.status.set("Previous update failed. See Files/update-install.log; use Check for updates to retry.")
        self.update_idle_since = None
        self.last_music = None
        self.next_music_refresh = 0
        self.refresh_files()
        threading.Thread(target=self.run_agent, daemon=True).start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def toggle_auto_update(self):
        self.config["auto_update"] = self.auto_update.get()
        self.save()
        status_file = FILES_DIR / "update-status.txt"
        failed_update = status_file.is_file() and "failed" in status_file.read_text(encoding="utf-8-sig", errors="replace").lower()
        self.next_update_check = time.monotonic() + 6 * 60 * 60 if failed_update else 0
        if failed_update:
            self.status.set("Previous update failed. See Files/update-install.log; use Check for updates to retry.")

    def check_updates(self, manual=False):
        if self.update_check_running or self.pending_update is not None:
            return
        if not getattr(sys, "frozen", False):
            self.next_update_check = time.monotonic() + 6 * 60 * 60
            if manual:
                self.status.set("Self-updates are available in the packaged Windows executable.")
            return
        self.update_check_running = True
        self.next_update_check = time.monotonic() + 6 * 60 * 60
        self.update_button.configure(state="disabled")
        if manual:
            self.status.set("Checking GitHub for relay updates…")
        def check():
            try:
                update = find_update(RELAY_BUILD)
                if update is None:
                    self.agent.events.put({"update_checked": True,
                        "status": "Relay is up to date." if manual else None})
                else:
                    stage = prepare_update(update)
                    self.agent.events.put({"update_checked": True, "update_stage": stage,
                        "update_manual": manual, "status": f"Relay build {update['build']} downloaded; waiting for audio to finish."})
            except Exception as exc:
                self.agent.events.put({"update_checked": True,
                    "status": "Update check failed; playback remains available. " + clean_error(exc)})
        threading.Thread(target=check, daemon=True).start()

    def install_update_now(self):
        if self.pending_update:
            stage, _ = self.pending_update
            self.pending_update = (stage, True)
            self.update_idle_since = time.monotonic() - 30
        else:
            self.install_button.configure(state="disabled")

    def poll_updates(self):
        now = time.monotonic()
        if hasattr(self, "install_button"):
            self.install_button.configure(state="normal" if self.pending_update else "disabled")
        if self.pending_update is not None:
            stage, manual = self.pending_update
            if not manual and not self.auto_update.get():
                self.pending_update = None
                threading.Thread(target=lambda: shutil.rmtree(stage, ignore_errors=True), daemon=True).start()
                return
            busy = bool(self.agent.stream_task and not self.agent.stream_task.done()) or any(not task.done() for task in self.agent.clip_tasks.values())
            busy = busy and not manual
            if busy:
                self.update_idle_since = None
            elif self.update_idle_since is None:
                self.update_idle_since = now
            elif manual or now - self.update_idle_since >= 5:
                try:
                    self.save()
                    launch_installer(stage, APP_DIR)
                except Exception as exc:
                    self.status.set("Could not install update: " + clean_error(exc))
                    self.pending_update = None
                    return
                self.close()
                return True
        elif self.auto_update.get() and now >= self.next_update_check:
            self.check_updates()

    def register_drop_targets(self, widget):
        from tkinterdnd2 import DND_FILES
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self.drop_files)
        for child in widget.winfo_children():
            self.register_drop_targets(child)

    def drop_files(self, event):
        self.clip_pressed_index = None
        from tkinterdnd2 import COPY, REFUSE_DROP
        folder = self.folder.get()
        if not folder or not Path(folder).is_dir():
            self.status.set("Choose a soundboard folder before dropping files.")
            return REFUSE_DROP
        paths = self.root.tk.splitlist(event.data)
        self.status.set("Copying dropped audio files…")
        def copy():
            try:
                copied, skipped = copy_soundboard_files(paths, folder)
                self.agent.events.put({"import_done": True, "folder": folder,
                    "status": f"Copied {len(copied)} audio file(s); skipped {len(skipped)}."})
            except Exception as exc:
                self.agent.events.put({"error": clean_error(exc)})
        threading.Thread(target=copy, daemon=True).start()
        return COPY

    def save(self):
        CONFIG_PATH.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder.get() or None)
        if folder:
            self.config["mp3_folder"] = folder
            self.folder.set(folder)
            self.save()
            self.refresh_files()

    def refresh_files(self):
        folder = self.folder.get()
        self.scan_generation = getattr(self, "scan_generation", 0) + 1
        generation = self.scan_generation
        self.status.set("Scanning audio files…")
        def scan():
            files = scan_audio_folder(folder) if folder else {}
            origins = {key: "Local" for key in files}
            SHARED_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            shared = scan_audio_folder(str(SHARED_CLIPS_DIR))
            files.update(shared)
            origins.update({key: "Shared" for key in shared})
            self.agent.events.put({"library": files, "origins": origins, "generation": generation})
        threading.Thread(target=scan, daemon=True).start()

    def send(self, payload):
        if self.agent.websocket is None:
            self.status.set("Not connected yet.")
            return
        future = asyncio.run_coroutine_threadsafe(self.agent.send_json(payload), self.loop)
        def done(result):
            try:
                result.result()
            except Exception as exc:
                self.agent.events.put({"error": clean_error(exc)})
        future.add_done_callback(done)

    def target_id(self):
        index = self.destination.current()
        if index < 0:
            self.status.set("Connect the bot using y!join, refresh Discord, then choose a destination.")
            return None
        return self.targets[index]["id"]

    def refresh_music(self):
        index = self.destination.current()
        if 0 <= index < len(self.targets):
            self.send({"type": "music_state", "guild_id": self.targets[index]["id"]})

    def music_control(self, action):
        guild_id = self.target_id()
        if guild_id is not None:
            self.send({"type": "music_control", "guild_id": guild_id, "action": action})
            self.refresh_music()

    def sync_shared(self):
        if self.sync_running:
            return
        self.sync_running = True
        self.status.set("Syncing shared clips…")
        def worker():
            try:
                up, down, conflicts = sync_clips(SHARED_CLIPS_DIR, self.config, AUDIO_EXTENSIONS, is_playable_audio)
                message = f"Shared sync: uploaded {up}, downloaded {down}."
                if conflicts:
                    message += " Rename conflicting/oversized clips: " + ", ".join(conflicts)
                self.agent.events.put({"sync_done": True, "status": message})
            except Exception as exc:
                self.agent.events.put({"sync_done": True, "error": clean_error(exc)})
        threading.Thread(target=worker, daemon=True).start()

    def sort_clips(self, column):
        self.sort_reverse = not self.sort_reverse if self.sort_column == column else False
        self.sort_column = column
        self.render_clips()

    def render_clips(self):
        ids = list(self.agent.local_files)
        if self.sort_column == "shared":
            first = "Shared" if self.sort_reverse else "Local"
            ids.sort(key=lambda key: (self.clip_origins[key] != first, self.agent.local_files[key].stem.casefold()))
        else:
            ids.sort(key=lambda key: self.agent.local_files[key].stem.casefold(), reverse=self.sort_reverse)
        self.file_ids = ids
        self.listbox.delete(*self.listbox.get_children())
        for key in ids:
            self.listbox.insert("", "end", iid=key, values=(self.agent.local_files[key].stem, self.clip_origins[key]))

    def song_mouse_down(self, event):
        self.queue_pressed = self.queue_view.identify_row(event.y) if self.queue_view.identify_region(event.x, event.y) == "cell" else None

    def song_selected(self, event):
        pressed = self.queue_pressed
        self.queue_pressed = None
        if event.widget is not self.queue_view or self.queue_view.identify_region(event.x, event.y) != "cell":
            return
        track_id = self.queue_view.identify_row(event.y)
        if not track_id or track_id != pressed or not self.last_music or "playlist" not in self.last_music:
            return
        guild_id = self.target_id()
        if guild_id is not None:
            self.send({"type": "music_control", "guild_id": guild_id, "action": "select", "track_id": track_id})

    def clip_mouse_down(self, event):
        self.clip_pressed_index = self.listbox.identify_row(event.y) if self.listbox.identify_region(event.x, event.y) == "cell" else None

    def play_selected(self, event):
        pressed = self.clip_pressed_index
        self.clip_pressed_index = None
        if event.widget is not self.listbox or self.listbox.identify_region(event.x, event.y) != "cell":
            return
        file_id = self.listbox.identify_row(event.y)
        if not file_id or pressed != file_id or self.listbox.identify_column(event.x) != "#1":
            return
        index = self.destination.current()
        guild_id = self.targets[index]["id"] if 0 <= index < len(self.targets) else None
        self.status.set("Starting clip " + self.agent.local_files[file_id].name)
        self.send({"type": "local_play", "guild_id": guild_id, "file_id": file_id,
                   "title": self.agent.local_files[file_id].name})

    def stop(self):
        guild_id = self.target_id()
        if guild_id is not None:
            self.send({"type": "local_stop", "guild_id": guild_id})

    def run_agent(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.agent.run())
        except asyncio.CancelledError:
            pass

    def poll(self):
        while not self.agent.events.empty():
            event = self.agent.events.get_nowait()
            if event.get("relay_name"):
                self.root.title("Runsforming Audio Relay — " + event["relay_name"])
                self.save()
            if event.get("token_required"):
                token = simpledialog.askstring("New relay token needed", "Token expired or invalid. Use !token in Discord, then paste your new token:", show="*", parent=self.root)
                if token and token.strip():
                    self.config["relay_token"] = token.strip()
                    self.save()
                    self.loop.call_soon_threadsafe(self.agent.token_ready.set)
                else:
                    self.status.set("Use !token in Discord, then restart the relay to enter the new token.")
                    self.config["relay_token"] = ""
                    self.save()
            if event.get("update_checked"):
                self.update_check_running = False
                self.update_button.configure(state="normal")
                if event.get("update_stage") is not None:
                    self.install_button.configure(state="normal")
                    self.pending_update = (event["update_stage"], event["update_manual"])
                    self.update_idle_since = None
            if event.get("import_done") and event.get("folder") == self.folder.get():
                self.refresh_files()
            if event.get("sync_done"):
                self.sync_running = False
                self.refresh_files()
            if "library" in event and event["generation"] == self.scan_generation:
                files = event["library"]
                self.agent.local_files = files
                self.clip_origins = event["origins"]
                self.render_clips()
                self.status.set(f"Loaded {len(files)} valid audio files.")
            if "targets" in event:
                previous = self.destination.get()
                self.targets = event["targets"]
                self.destination["values"] = [target["name"] for target in self.targets]
                if previous in self.destination["values"]:
                    self.destination.set(previous)
                elif self.targets:
                    self.destination.current(0)
                else:
                    self.destination.set("")
            if "music" in event:
                index = self.destination.current()
                if 0 <= index < len(self.targets) and str(event.get("guild_id")) == self.targets[index]["id"]:
                    music = event["music"]
                    if music != self.last_music:
                        self.last_music = music
                        self.queue_view.delete(*self.queue_view.get_children())
                        playlist = music.get("playlist")
                        if playlist is not None:
                            for song in playlist:
                                label = ("Paused: " if music["paused"] else "Playing: ") if song["current"] else ""
                                self.queue_view.insert("", "end", iid=song["id"], values=(label + song["title"],), tags=("current",) if song["current"] else ())
                        else:
                            if music["current"]:
                                self.queue_view.insert("", "end", values=(music["current"],), tags=("current",))
                            for number, title in enumerate(music["queue"], 1):
                                self.queue_view.insert("", "end", values=(f"{number}. {title}",))
                        self.music_status.set("Paused" if music["paused"] else ("Playing" if music["current"] else "No music playing"))
                        self.toggle_button.configure(text="Play" if music["paused"] or not music["current"] else "Pause")
                        self.previous_button.configure(state="normal" if music["has_previous"] else "disabled")
            self.status.set(event.get("error") or event.get("status") or self.status.get())
        if time.monotonic() >= self.next_music_refresh:
            self.next_music_refresh = time.monotonic() + 2
            self.send({"type": "targets"})
            self.refresh_music()
        if not self.poll_updates():
            self.root.after(100, self.poll)

    def close(self):
        def cancel_all():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()
        self.loop.call_soon_threadsafe(cancel_all)
        self.root.destroy()


def main():
    root = create_root()
    root.withdraw()
    try:
        if CONFIG_PATH.is_file():
            config = load_config()
        else:
            token = simpledialog.askstring("Setup", "Relay token:", show="*", parent=root)
            if not token:
                root.destroy()
                return
            config = {"server_url": RELAY_URL, "relay_token": token.strip(),
                      "relay_name": "Discord relay", "cookies_file": "", "mp3_folder": ""}
            CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        if not config.get("relay_token"):
            token = simpledialog.askstring("Setup", "Use !token in Discord, then paste your relay token:", show="*", parent=root)
            if not token:
                root.destroy()
                return
            config["relay_token"] = token.strip()
        config["server_url"] = RELAY_URL
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        verify_tools()
        threading.Thread(target=update_ytdlp, daemon=True).start()
        root.deiconify()
        RelayWindow(root, config)
        root.mainloop()
    except Exception as exc:
        from tkinter import messagebox
        messagebox.showerror("Relay could not start", clean_error(exc), parent=root)
        root.destroy()


if __name__ == "__main__":
    if "--update-launch-test" in sys.argv:
        stage = Path(sys.argv[sys.argv.index("--update-launch-test") + 1])
        launch_installer(stage, APP_DIR, restart_argument="update-restart-test")
    elif "update-restart-test" in sys.argv:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        (FILES_DIR / "update-restart-ok.txt").write_text(f"Restarted v1.{RELAY_BUILD}", encoding="utf-8")
    elif "--self-test" in sys.argv:
        try:
            verify_tools()
            test_root = create_root()
            test_root.withdraw()
            from tkinterdnd2 import DND_FILES
            test_root.drop_target_register(DND_FILES)
            assert relay_ssl_context().get_ca_certs()
            test_root.destroy()
        except Exception as exc:
            FILES_DIR.mkdir(parents=True, exist_ok=True)
            (FILES_DIR / "self-test-error.txt").write_text(str(exc), encoding="utf-8")
            sys.exit(1)
    else:
        main()
