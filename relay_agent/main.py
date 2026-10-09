import asyncio
import math
from array import array
import json
import re
from urllib.parse import urlsplit
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
from tkinter import filedialog, ttk, simpledialog, messagebox
from pathlib import Path

import certifi
from websockets.asyncio.client import connect
if __package__:
    from .shared_clips import sync_clips, edit_shared_clip, check_share_target
    from .updater import find_update, prepare_update, launch_installer
else:
    from shared_clips import sync_clips, edit_shared_clip, check_share_target
    from updater import find_update, prepare_update, launch_installer


RELAY_BUILD = 0
RELAY_URL = "wss://runsformingbot-production.up.railway.app/relay"
PROTOCOL_VERSION = 1
PCM_FRAME_BYTES = 3840
MAX_TRACK_SECONDS = 6 * 60 * 60
AUDIO_EXTENSIONS = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".flac", ".m4a", ".aac", ".wma", ".aif", ".aiff"}


LABEL_COLORS = {
    "Light Red": "#ffb3b3", "Light Orange": "#ffd1a3", "Light Yellow": "#fff2a3",
    "Light Green": "#bde8b3", "Light Blue": "#b3d9ff", "Light Purple": "#d9b3ff",
    "Light Pink": "#ffb3d9",
}


def clip_gain(frame, adjustment):
    gain = 1 + max(-100, min(100, float(adjustment))) / 100
    if gain == 1:
        return frame
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, round(sample * gain)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def sorted_clip_ids(files, origins, settings, sort_keys):
    ids = sorted(files, key=lambda key: files[key].stem.casefold())
    for column, reverse in reversed(sort_keys):
        if column == 'shared':
            key = lambda ident: origins.get(ident) != 'Shared'
        elif column == 'label':
            key = lambda ident: settings.get(ident, {}).get('label', '').casefold()
        else:
            key = lambda ident: files[ident].stem.casefold()
        ids.sort(key=key, reverse=reverse)
    return ids


CLIP_COLUMNS = ("name", "volume", "shared", "label")


def clip_column_order(saved):
    return list(saved) if isinstance(saved, (list, tuple)) and len(saved) == 4 and set(saved) == set(CLIP_COLUMNS) else list(CLIP_COLUMNS)


def reordered_columns(order, source, target):
    result = list(order)
    if source in result and target in result and source != target:
        old, new = result.index(source), result.index(target)
        result.pop(old)
        result.insert(new, source)
    return result


def style_relay(root):
    root.configure(background="#f1f2f4")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background="#f1f2f4", foreground="#20252b")
    style.configure("TFrame", background="#f1f2f4")
    style.configure("Card.TFrame", background="#ffffff")
    style.configure("TLabel", background="#f1f2f4")
    style.configure("Card.TLabel", background="#ffffff")
    style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
    style.configure("Muted.TLabel", foreground="#7c838e", font=("Segoe UI", 9))
    style.configure("CardMuted.TLabel", background="#ffffff", foreground="#7c838e", font=("Segoe UI", 9))
    style.configure("Song.TLabel", background="#ffffff", font=("Segoe UI", 17, "bold"))
    style.configure("TButton", background="#ffffff", borderwidth=0, padding=(16, 10), relief="flat")
    style.map("TButton", background=[("active", "#e5e8ed")], foreground=[("disabled", "#a5abb5")])
    style.configure("Primary.TButton", background="#24272c", foreground="#ffffff")
    style.map("Primary.TButton", background=[("active", "#414650")], foreground=[("disabled", "#969ba4")])
    style.configure("TNotebook", background="#f1f2f4", borderwidth=0, tabmargins=0)
    style.layout("TNotebook.Tab", [])
    style.configure("SelectedTab.TButton", background="#ffffff", foreground="#172c4a", font=("Segoe UI", 11, "bold"), padding=(20, 15))
    style.configure("OtherTab.TButton", background="#e7e9ed", foreground="#7c838e", font=("Segoe UI", 10), padding=(16, 8))
    style.configure("TNotebook.Tab", padding=(26, 12), font=("Segoe UI", 11, "bold"), background="#e7e9ed", borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#20252b"), ("!selected", "#7c838e")])
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#303640", rowheight=36, borderwidth=0)
    style.configure("Soundboard.Treeview", rowheight=18, font=("Segoe UI", 9))
    style.configure("Treeview.Heading", background="#172c4a", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 12), relief="flat")
    style.map("Treeview.Heading", background=[("active", "#233e60")], foreground=[("active", "#ffffff")])
    style.map("Treeview", background=[("selected", "#e8edf5")], foreground=[("selected", "#20252b")])
    style.configure("Clip.TFrame", background="#ffffff")
    style.configure("Clip.TLabel", background="#ffffff", foreground="#7c838e", font=("Segoe UI", 9))
    style.configure("TCheckbutton", background="#f1f2f4", padding=6)
    style.configure("Horizontal.TScale", background="#ffffff", troughcolor="#e7e9ed", borderwidth=0)


def themed_menu(parent):
    return tk.Menu(parent, tearoff=False, background="#ffffff", foreground="#303640",
                   activebackground="#e8edf5", activeforeground="#172c4a",
                   relief="flat", borderwidth=0, font=("Segoe UI", 10))


def copy_clip_to_shared(source, folder):
    source, folder = Path(source), Path(folder)
    if not source.is_file() or source.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Select an available audio file.")
    if not 0 < source.stat().st_size <= 20 * 1024 * 1024:
        raise ValueError("Shared clips must be between 1 byte and 20 MB.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / source.name
    if source.resolve() == target.resolve():
        raise ValueError("This clip is already in the shared folder.")
    if target.exists():
        if source.read_bytes() != target.read_bytes():
            raise ValueError("A different shared clip already uses this filename. Rename before sharing.")
        return target
    try:
        with target.open("xb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
    except FileExistsError:
        raise ValueError("This shared filename was created during copying. Retry or rename.") from None
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


NORMALIZATION_VERSION = 1


def clip_normalization_gain(report):
    mean = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB", report)
    peak = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB", report)
    if not mean or not peak:
        raise ValueError("Could not measure soundboard clip loudness.")
    mean, peak = float(mean[1]), float(peak[1])
    if not math.isfinite(mean) or not math.isfinite(peak) or peak <= -90:
        return 0.0
    # Average loudness target with 7 dB of peak headroom. User gain runs later.
    return round(min(-20.0 - mean, -7.0 - peak, 24.0), 3)


def clipboard_youtube_link(text):
    # Ignore unrelated clipboard text; submit only the first actual YouTube URL.
    pattern = r"https?://[^\s<>\"']+|(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+"
    for match in re.finditer(pattern, str(text), re.IGNORECASE):
        url = match.group().rstrip(".,;!)]}")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            continue
        if host in {"youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
            return url
    return None


def soundboard_rename_target(path, name):
    path = Path(path)
    name = name.strip()
    if name.lower().endswith(path.suffix.lower()):
        name = name[:-len(path.suffix)]
    if (not name or name.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}):
        raise ValueError("Enter a valid filename without folder separators or reserved Windows names.")
    target = path.with_name(name + path.suffix)
    if target == path:
        return path
    if target.exists() and not target.samefile(path):
        raise ValueError("A clip with that filename already exists.")
    return target


def rename_soundboard_clip(path, name):
    path = Path(path)
    target = soundboard_rename_target(path, name)
    path.rename(target)
    return target


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
        self.normalization_cache = dict(config.get("clip_normalization", {}))
        self.clip_tasks = {}
        self.clip_file_ids = {}
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
            normalization_args = []
            if local_path is not None:
                stat = local_path.stat()
                key = str(local_path.resolve())
                fingerprint = [stat.st_mtime_ns, stat.st_size, NORMALIZATION_VERSION]
                cached = self.normalization_cache.get(key)
                if cached and cached.get("fingerprint") == fingerprint:
                    gain_db = cached["gain_db"]
                else:
                    ffmpeg = await asyncio.create_subprocess_exec(
                        str(FFMPEG_PATH), "-hide_banner", "-i", str(local_path),
                        "-vn", "-sn", "-dn", "-af", "volumedetect", "-f", "null", "-",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    _, report = await asyncio.wait_for(ffmpeg.communicate(), timeout=60)
                    if ffmpeg.returncode:
                        raise ValueError("Could not analyze this clip for normalization.")
                    gain_db = clip_normalization_gain(report.decode(errors="replace"))
                    self.normalization_cache = {**self.normalization_cache,
                                                key: {"fingerprint": fingerprint, "gain_db": gain_db}}
                    self.events.put({"normalization_cache": self.normalization_cache})
                normalization_args = ["-af", f"volume={gain_db}dB"]
            ffmpeg = await asyncio.create_subprocess_exec(
                str(FFMPEG_PATH),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(local_path) if local_path is not None else "pipe:0",
                "-vn",
                *normalization_args,
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
                        if local_path is not None:
                            frame = clip_gain(frame, self.config.get("clip_settings", {}).get(url.removeprefix("local:"), {}).get("volume", 0))
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
                if local_path is not None:
                    frame = clip_gain(frame, self.config.get("clip_settings", {}).get(url.removeprefix("local:"), {}).get("volume", 0))
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
            await asyncio.gather(*(task for task in (pump_task, ytdlp_error_task, ffmpeg_error_task)
                                   if task is not None), return_exceptions=True)
            for process in (ffmpeg, ytdlp):
                if process is not None:
                    try:
                        await asyncio.wait_for(process.communicate(), timeout=5)
                    except (asyncio.TimeoutError, OSError):
                        pass
            if url.startswith("local:"):
                self.clip_file_ids.pop(request_id, None)
                self.clip_tasks.pop(request_id, None)
            elif self.stream_request_id == request_id:
                self.stream_task = None
                self.stream_request_id = None

    async def share_clip_file(self, file_id, path, folder, remove_local=False):
        target = await asyncio.to_thread(copy_clip_to_shared, path, folder)
        if remove_local:
            await self.edit_clip_file(file_id, path)
        return target

    async def edit_clip_file(self, file_id, path, new_name=None):
        for attempt in range(30):
            tasks = [task for request_id, task in list(self.clip_tasks.items())
                     if self.clip_file_ids.get(request_id) == file_id]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if new_name is None:
                    await asyncio.to_thread(path.unlink)
                    return "Deleted clip " + path.stem
                target = await asyncio.to_thread(rename_soundboard_clip, path, new_name)
                return "Renamed clip to " + target.stem
            except OSError as exc:
                if getattr(exc, "winerror", None) not in {32, 33} or attempt == 29:
                    raise
                await asyncio.sleep(0.1)

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
                    self.clip_file_ids[request_id] = url.removeprefix("local:")
                    task = asyncio.create_task(self.stream(request_id, url))
                    self.clip_tasks[request_id] = task
                    def finished(_task):
                        if self.clip_file_ids.pop(request_id, None) is not None:
                            async def notify():
                                try:
                                    await self.send_json({"type": "stream_end", "request_id": request_id})
                                except Exception:
                                    pass
                            asyncio.create_task(notify())
                        self.clip_tasks.pop(request_id, None)
                    task.add_done_callback(finished)
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
            self.config["shared_owner"] = bool(hello.get("shared_owner", False))
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
        self.file_ids = []
        root.title("Runsforming Audio Relay")
        root.geometry("1060x740")
        root.minsize(1040, 570)
        style_relay(root)
        self.status = tk.StringVar(value="Connecting…")
        self.folder = tk.StringVar(value=config.get("mp3_folder", ""))
        top_bar = ttk.Frame(root)
        top_bar.pack(fill="x", padx=28, pady=(18, 8))
        self.identity = tk.StringVar(value="Connecting your relay")
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=28)
        self.tab_buttons = []
        for index, text in enumerate(("YouTube Music", "Soundboard")):
            button = ttk.Button(top_bar, text=text, style="SelectedTab.TButton" if index == 0 else "OtherTab.TButton",
                                command=lambda i=index: self.notebook.select(i))
            button.pack(side="left", anchor="s", padx=(0, 6))
            self.tab_buttons.append(button)
        self.toolbar_buttons = []
        button = ttk.Button(top_bar, text="Change folder", command=self.choose_folder)
        button.pack(side="right", anchor="s", padx=(6, 0))
        self.toolbar_buttons.append(button)
        self.install_button = ttk.Button(top_bar, text="Install update now", command=self.install_update_now, state="disabled")
        self.install_button.pack(side="right", anchor="s", padx=(6, 0))
        self.update_button = ttk.Button(top_bar, text="Check for updates", command=lambda: self.check_updates(manual=True))
        self.update_button.pack(side="right", anchor="s", padx=(6, 0))
        self.auto_update = tk.BooleanVar(value=bool(config.get("auto_update", True)))
        self.auto_update_checkbox = ttk.Checkbutton(top_bar, text="Update automatically", variable=self.auto_update,
                                                    command=self.toggle_auto_update)
        self.auto_update_checkbox.pack(side="right", anchor="s", padx=(6, 0))
        left, right = ttk.Frame(self.notebook, padding=20), ttk.Frame(self.notebook, padding=20)
        self.notebook.add(left, text="YouTube Music")
        self.notebook.add(right, text="Soundboard")
        self.notebook.bind("<<NotebookTabChanged>>", self.update_tabs)
        now_card = ttk.Frame(left, style="Card.TFrame", padding=22)
        now_card.pack(fill="x", pady=(0, 16))
        ttk.Label(now_card, text="NOW PLAYING", style="CardMuted.TLabel").pack(anchor="w")
        self.current_song = tk.StringVar(value="Your next song starts here")
        ttk.Label(now_card, textvariable=self.current_song, style="Song.TLabel", wraplength=850).pack(anchor="w", pady=(8, 4))
        music_controls = ttk.Frame(now_card, style="Card.TFrame")
        music_controls.pack(fill="x", pady=8)
        self.previous_button = ttk.Button(music_controls, text="Previous", command=lambda: self.music_control("previous"))
        self.previous_button.pack(side="left")
        self.toggle_button = ttk.Button(music_controls, text="Pause / Play", style="Primary.TButton", command=lambda: self.music_control("toggle"))
        self.toggle_button.pack(side="left", padx=8)
        ttk.Button(music_controls, text="Next", command=lambda: self.music_control("next")).pack(side="left")
        self.music_status = tk.StringVar(value="No music playing")
        ttk.Label(now_card, textvariable=self.music_status, style="CardMuted.TLabel").pack(anchor="w", pady=4)
        self.queue_view = ttk.Treeview(left, columns=("song",), show="headings", selectmode="browse")
        self.queue_view.heading("song", text="Your music queue")
        self.queue_pressed = None
        self.queue_view.bind("<ButtonPress-1>", self.song_mouse_down)
        self.queue_view.bind("<ButtonRelease-1>", self.song_selected)
        self.queue_view.column("song", width=400)
        self.queue_view.tag_configure("current", background="#dceeff", foreground="#14467a", font=("Segoe UI", 10, "bold"))
        queue_scroll = ttk.Scrollbar(left, orient="vertical", command=self.queue_view.yview)
        self.queue_view.configure(yscrollcommand=queue_scroll.set)
        queue_scroll.pack(side="right", fill="y")
        self.paste_playlist = tk.BooleanVar(value=bool(config.get("paste_playlist", False)))
        ttk.Checkbutton(left, text="Check to have links play playlists instead of single song",
                        variable=self.paste_playlist, command=self.save_paste_setting).pack(side="bottom", anchor="w", pady=8)
        self.queue_view.pack(fill="both", expand=True, padx=(0, 12))
        root.bind("<Control-v>", self.paste_youtube_link)
        root.bind("<Control-V>", self.paste_youtube_link)
        ttk.Label(right, text="Click a filename to play • Right-click to manage • Drag headers to arrange columns", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(right, textvariable=self.folder, wraplength=900, style="Muted.TLabel").pack(anchor="w", pady=(0, 14))
        self.sync_running = False
        self.sort_keys = [("name", False)]
        self.sort_column = "name"
        self.sort_reverse = False
        self.clip_origins = {}
        self.listbox = ttk.Treeview(right, columns=("name", "volume", "shared", "label"), show="headings", selectmode="browse", style="Soundboard.Treeview")
        self.listbox.heading("name", text="Name")
        self.listbox.heading("shared", text="Shared")
        self.listbox.column("name", width=400, minwidth=120, stretch=False)
        self.listbox.heading("volume", text="Volume")
        self.listbox.column("volume", width=150, minwidth=150, stretch=False)
        self.listbox.heading("label", text="Label")
        self.listbox.column("label", width=200, minwidth=100, stretch=False)
        self.clip_widgets = {}
        self.header_drag = None
        self.column_order = clip_column_order(config.get("clip_column_order"))
        self.listbox.configure(displaycolumns=self.column_order)
        self.listbox.bind("<Configure>", lambda event: self.position_clip_widgets())
        self.listbox.column("shared", width=80, stretch=False, anchor="center")
        clip_scroll = ttk.Scrollbar(right, orient="vertical", command=self.scroll_clips)
        self.listbox.configure(yscrollcommand=lambda *args: (clip_scroll.set(*args), self.root.after_idle(self.position_clip_widgets)))
        clip_scroll.pack(side="right", fill="y")
        self.listbox.pack(fill="both", expand=True)
        self.clip_pressed_index = None
        self.listbox.bind("<ButtonPress-1>", self.clip_mouse_down)
        self.listbox.bind("<ButtonRelease-1>", self.play_selected)
        self.listbox.bind("<B1-Motion>", self.clip_mouse_move, add="+")
        self.listbox.bind("<ButtonRelease-1>", lambda event: self.root.after_idle(self.position_clip_widgets), add="+")
        self.clip_menu = themed_menu(root)
        self.clip_menu.add_command(label="Share", command=lambda: self.share_clip(False))
        self.clip_menu.add_command(label="Share then del local", command=lambda: self.share_clip(True))
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Rename", command=self.rename_clip)
        self.clip_menu.add_command(label="Delete", command=self.delete_clip)
        self.clip_menu.add_separator()
        self.clip_menu.add_command(label="Add Label", command=self.add_label)
        color_menu = themed_menu(self.clip_menu)
        for name, color in LABEL_COLORS.items():
            color_menu.add_command(label=name, foreground=color, command=lambda n=name: self.change_label_color(n))
        self.clip_menu.add_cascade(label="Change Label Color", menu=color_menu)
        self.context_clip_id = None
        self.listbox.bind("<Button-3>", self.clip_context_menu)
        ttk.Label(root, textvariable=self.status, wraplength=1000, style="Muted.TLabel").pack(anchor="w", padx=28, pady=(14, 4))
        ttk.Label(right, text="Drop audio files anywhere in this window to copy them to your local folder.", style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=6)
        self.register_drop_targets(root)
        update_bar = ttk.Frame(root)
        update_bar.pack(fill="x", padx=16, pady=4)
        self.clip_action_buttons = []
        for text, command in (("Refresh files", self.refresh_files), ("Stop all clips", self.stop), ("Sync clips", self.sync_shared)):
            button = ttk.Button(update_bar, text=text, command=command)
            button.pack(side="left", padx=(0, 8))
            self.clip_action_buttons.append(button)
        ttk.Label(update_bar, text=f"RunsFormingMusic v1.{RELAY_BUILD}", style="Muted.TLabel").pack(side="right")
        ttk.Label(update_bar, textvariable=self.identity, style="Muted.TLabel").pack(side="right", padx=18)
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

    def update_tabs(self, event=None):
        selected = self.notebook.index(self.notebook.select())
        for index, button in enumerate(self.tab_buttons):
            button.configure(style="SelectedTab.TButton" if index == selected else "OtherTab.TButton")
        self.root.after_idle(self.position_clip_widgets)

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
        # The server resolves the personal token's current voice channel each time.
        return None

    def refresh_music(self):
        self.send({"type": "music_state", "guild_id": None})

    def music_control(self, action):
        self.send({"type": "music_control", "guild_id": None, "action": action})
        self.refresh_music()

    def sync_shared(self):
        if self.sync_running or getattr(self, "editing_clips", set()):
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
        keys = getattr(self, "sort_keys", [("name", False)])
        reverse = not keys[0][1] if keys and keys[0][0] == column else False
        self.sort_keys = [(column, reverse)] + [(c, r) for c, r in keys if c != column]
        self.render_clips()

    def render_clips(self):
        ids = sorted_clip_ids(self.agent.local_files, self.clip_origins,
                              self.config.get("clip_settings", {}), getattr(self, "sort_keys", [("name", False)]))
        self.file_ids = ids
        for widgets in self.clip_widgets.values():
            for widget in widgets:
                widget.destroy()
        self.clip_widgets.clear()
        self.listbox.delete(*self.listbox.get_children())
        for key in ids:
            self.listbox.insert("", "end", iid=key, values=(self.agent.local_files[key].stem, "", "☑" if self.clip_origins[key] == "Shared" else "☐", ""))
        self.root.after_idle(self.position_clip_widgets)

    def scroll_clips(self, *args):
        self.listbox.yview(*args)
        self.position_clip_widgets()

    def clip_setting(self, key):
        return self.config.setdefault("clip_settings", {}).setdefault(key, {})

    def set_clip_volume(self, key, value, text):
        volume = round(float(value))
        self.clip_setting(key)["volume"] = volume
        text.configure(text=f"{volume:+d}%" if volume else "0%")
        # Save once dragging settles, keeping the audio loop and UI responsive.
        pending = getattr(self, "volume_save_after", None)
        if pending:
            self.root.after_cancel(pending)
        self.volume_save_after = self.root.after(300, self.save_volume)

    def save_volume(self):
        self.volume_save_after = None
        self.save()

    def position_clip_widgets(self):
        for key in self.file_ids:
            boxes = [self.listbox.bbox(key, column) for column in ("volume", "label")]
            if not all(boxes):
                for widget in self.clip_widgets.pop(key, ()):
                    widget.destroy()
                continue
            if key not in self.clip_widgets:
                frame = ttk.Frame(self.listbox, style="Clip.TFrame")
                text = ttk.Label(frame, width=6, style="Clip.TLabel")
                text.pack(side="right")
                scale = ttk.Scale(frame, from_=-100, to=100,
                                  command=lambda value, k=key, t=text: self.set_clip_volume(k, value, t))
                scale.set(self.clip_setting(key).get("volume", 0))
                scale.pack(side="left", fill="x", expand=True)
                label = tk.Menubutton(self.listbox, relief="flat", anchor="w", indicatoron=False, borderwidth=0,
                                      highlightthickness=0, padx=10, font=("Segoe UI", 9))
                menu = themed_menu(label)
                menu.configure(postcommand=lambda k=key: self.select_label_row(k))
                label.configure(menu=menu)
                self.clip_widgets[key] = (frame, label)
            frame, label = self.clip_widgets[key]
            current = self.clip_setting(key).get("label", "")
            color = self.config.get("clip_labels", {}).get(current, "#ffffff")
            label.configure(text=current or "Select…", background=color, activebackground=color)
            menu = label["menu"]
            menu = label.nametowidget(menu)
            menu.delete(0, "end")
            for name in ["", *sorted(self.config.get("clip_labels", {}), key=str.casefold)]:
                menu.add_command(label=name or "No label", command=lambda k=key, n=name: self.set_clip_label(k, n))
            for widget, (x, y, width, height) in zip((frame, label), boxes):
                widget.place(x=x, y=y, width=width, height=height)
            for widget in (frame, label, *frame.winfo_children()):
                widget.bind("<Button-3>", lambda event, k=key: self.open_clip_menu(k, event))

    def select_label_row(self, key):
        self.clip_pressed_index = None
        self.listbox.selection_set(key)
        self.listbox.focus(key)

    def set_clip_label(self, key, name):
        self.clip_setting(key)["label"] = name
        self.save()
        self.render_clips()
        self.select_label_row(key)

    def add_label(self):
        name = simpledialog.askstring("Add Label", "Label name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        self.config.setdefault("clip_labels", {}).setdefault(name, LABEL_COLORS["Light Blue"])
        self.set_clip_label(self.context_clip_id, name)

    def change_label_color(self, color_name):
        name = self.clip_setting(self.context_clip_id).get("label")
        if name:
            self.config.setdefault("clip_labels", {})[name] = LABEL_COLORS[color_name]
            self.save()
            self.position_clip_widgets()

    def open_clip_menu(self, file_id, event):
        self.context_clip_id = file_id
        self.clip_pressed_index = None
        self.listbox.selection_set(file_id)
        can_share = self.clip_origins.get(file_id) == "Local" and not self.sync_running and file_id not in getattr(self, "editing_clips", set())
        for action in ("Share", "Share then del local"):
            self.clip_menu.entryconfigure(action, state="normal" if can_share else "disabled")
        has_label = bool(self.clip_setting(file_id).get("label"))
        self.clip_menu.entryconfigure("Change Label Color", state="normal" if has_label else "disabled")
        try:
            self.clip_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.clip_menu.grab_release()
        return "break"

    def save_paste_setting(self):
        self.config["paste_playlist"] = bool(self.paste_playlist.get())
        self.save()

    def paste_youtube_link(self, event=None):
        try:
            url = clipboard_youtube_link(self.root.clipboard_get())
        except tk.TclError:
            return
        if url is None:
            return
        guild_id = None
        self.status.set("Adding YouTube playlist…" if self.paste_playlist.get() else "Adding YouTube song…")
        self.send({"type": "music_enqueue", "guild_id": guild_id, "url": url,
                   "playlist": bool(self.paste_playlist.get())})
        return "break"

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
        self.send({"type": "music_control", "guild_id": guild_id, "action": "select", "track_id": track_id})

    def clip_context_menu(self, event):
        self.clip_pressed_index = None
        file_id = self.listbox.identify_row(event.y)
        if self.listbox.identify_region(event.x, event.y) != "cell" or file_id not in self.agent.local_files:
            return
        return self.open_clip_menu(file_id, event)

    def editable_clip(self):
        file_id = self.context_clip_id
        if file_id in getattr(self, "editing_clips", set()):
            self.status.set("This clip is already being edited.")
            return None
        path = self.agent.local_files.get(file_id)
        if not path or not path.is_file():
            self.status.set("This clip is no longer available. Refreshing files.")
            self.refresh_files()
            return None
        if self.clip_origins.get(file_id) == "Shared" and self.sync_running:
            self.status.set("Wait for shared clip sync to finish before editing shared files.")
            return None
        return path

    def share_clip(self, remove_local=False):
        file_id = self.context_clip_id
        path = self.editable_clip()
        if path is None or self.clip_origins.get(file_id) != "Local":
            return
        if self.sync_running or getattr(self, "editing_clips", set()):
            self.status.set("Wait for the current clip operation to finish.")
            return
        if not hasattr(self, "editing_clips"):
            self.editing_clips = set()
        self.editing_clips.add(file_id)
        settings = dict(self.clip_setting(file_id))
        self.status.set("Copying clip to shared folder…")
        async def share_checked():
            await asyncio.to_thread(check_share_target, self.config, path)
            return await self.agent.share_clip_file(file_id, path, SHARED_CLIPS_DIR, remove_local)
        future = asyncio.run_coroutine_threadsafe(share_checked(), self.loop)
        def done(result):
            try:
                target = result.result()
                self.agent.events.put({"share_done": file_id, "shared_path": str(target),
                                       "share_settings": settings})
            except Exception as exc:
                self.agent.events.put({"share_done": file_id, "share_error": str(exc)})
        future.add_done_callback(done)

    def rename_clip(self):
        path = self.editable_clip()
        if path is None:
            return
        name = simpledialog.askstring("Rename clip", "New filename (audio extension is preserved):", initialvalue=path.stem, parent=self.root)
        if name is None:
            return
        self.start_clip_edit(path, name)

    def delete_clip(self):
        path = self.editable_clip()
        if path is None:
            return
        shared = self.clip_origins.get(self.context_clip_id) == "Shared"
        prompt = f"Delete {path.name} from this PC?"
        if shared:
            prompt += "\nThis also deletes the GitHub shared file for everyone." if self.config.get("shared_owner") else "\nThis deletes your shared copy only. Sync can download it again."
        if not messagebox.askyesno("Delete clip", prompt, parent=self.root):
            return
        self.start_clip_edit(path)

    def start_clip_edit(self, path, new_name=None):
        file_id = self.context_clip_id
        if not hasattr(self, "editing_clips"):
            self.editing_clips = set()
        self.editing_clips.add(file_id)
        self.status.set("Releasing clip audio before editing…")
        try:
            target = soundboard_rename_target(path, new_name) if new_name is not None else None
        except ValueError as exc:
            self.editing_clips.discard(file_id)
            messagebox.showerror("Could not edit clip", str(exc), parent=self.root)
            return
        async def edit():
            if self.clip_origins.get(file_id) == "Shared" and self.config.get("shared_owner"):
                await asyncio.to_thread(edit_shared_clip, self.config, path, target.name if target else None)
            return await self.agent.edit_clip_file(file_id, path, new_name)
        future = asyncio.run_coroutine_threadsafe(edit(), self.loop)
        def done(result):
            try:
                status = result.result()
                self.agent.events.put({"clip_edit_done": file_id, "status": status,
                                       "renamed_path": str(target) if target else None})
            except Exception as exc:
                self.agent.events.put({"clip_edit_done": file_id, "clip_edit_error": str(exc)})
        future.add_done_callback(done)

    def clip_column_at(self, x):
        display = self.listbox.identify_column(x)
        try:
            index = int(display[1:]) - 1
            return getattr(self, "column_order", list(CLIP_COLUMNS))[index] if index >= 0 else None
        except (ValueError, IndexError):
            return None

    def clip_mouse_down(self, event):
        region = self.listbox.identify_region(event.x, event.y)
        self.header_drag = None
        if region == "heading":
            self.header_drag = {"column": self.clip_column_at(event.x), "x": event.x, "moved": False}
            self.clip_pressed_index = None
            return "break"
        self.clip_pressed_index = self.listbox.identify_row(event.y) if region == "cell" else None

    def clip_mouse_move(self, event):
        drag = getattr(self, "header_drag", None)
        if drag:
            if abs(event.x - drag["x"]) > 8:
                drag["moved"] = True
                self.listbox.configure(cursor="fleur")
            return "break"
        self.root.after_idle(self.position_clip_widgets)

    def play_selected(self, event):
        drag = getattr(self, "header_drag", None)
        if drag:
            self.header_drag = None
            self.listbox.configure(cursor="")
            if drag["moved"]:
                target = self.clip_column_at(event.x)
                self.column_order = reordered_columns(self.column_order, drag["column"], target)
                self.listbox.configure(displaycolumns=self.column_order)
                self.config["clip_column_order"] = self.column_order
                self.save()
                self.root.after_idle(self.position_clip_widgets)
            elif drag["column"] in {"name", "shared", "label"}:
                self.sort_clips(drag["column"])
            return "break"
        pressed = self.clip_pressed_index
        self.clip_pressed_index = None
        if event.widget is not self.listbox or self.listbox.identify_region(event.x, event.y) != "cell":
            return
        file_id = self.listbox.identify_row(event.y)
        if file_id in getattr(self, "editing_clips", set()):
            return
        if not file_id or pressed != file_id or self.clip_column_at(event.x) != "name":
            return
        guild_id = None
        self.status.set("Starting clip " + self.agent.local_files[file_id].name)
        self.send({"type": "local_play", "guild_id": guild_id, "file_id": file_id,
                   "title": self.agent.local_files[file_id].name})

    def stop(self):
        guild_id = self.target_id()
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
            if "normalization_cache" in event:
                self.config["clip_normalization"] = event["normalization_cache"]
                self.save()
            if event.get("share_done"):
                self.editing_clips.discard(event["share_done"])
                if event.get("share_error"):
                    messagebox.showerror("Could not share clip", event["share_error"], parent=self.root)
                    self.refresh_files()
                else:
                    key = hashlib.sha256(str(Path(event["shared_path"]).resolve()).encode()).hexdigest()
                    self.config.setdefault("clip_settings", {})[key] = event["share_settings"]
                    self.save()
                    self.refresh_files()
                    self.sync_shared()
            if event.get("clip_edit_done"):
                self.editing_clips.discard(event["clip_edit_done"])
                if event.get("renamed_path"):
                    settings = self.config.setdefault("clip_settings", {})
                    old = settings.pop(event["clip_edit_done"], None)
                    if old is not None:
                        new_key = hashlib.sha256(str(Path(event["renamed_path"]).resolve()).encode()).hexdigest()
                        settings[new_key] = old
                        self.save()
                if event.get("clip_edit_error"):
                    messagebox.showerror("Could not edit clip", event["clip_edit_error"], parent=self.root)
                self.refresh_files()
            if event.get("relay_name"):
                self.root.title("RunsForming Music — " + event["relay_name"])
                self.identity.set(event["relay_name"] + " • Personal relay")
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
            if "music" in event:
                music = event["music"]
                self.current_song.set(music.get("current") or "Your next song starts here")
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
