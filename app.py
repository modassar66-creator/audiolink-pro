import ipaddress
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
# 300MB max payload for uploads
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False

BASE_DIR = Path(os.environ.get("AUDIO_TMP_DIR", "/tmp/audio-link-converter"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
MAX_BYTES = int(os.environ.get("MAX_DOWNLOAD_BYTES", 250 * 1024 * 1024))
ALLOWED_FORMATS = {"mp3", "wav", "m4a", "flac", "ogg", "aac"}
ALLOWED_BITRATES = {"128k", "192k", "256k", "320k"}

# Concurrency & Job State Management
CONCURRENCY_LIMIT = 3
SEMAPHORE = threading.Semaphore(CONCURRENCY_LIMIT)
LOCK = threading.Lock()
JOBS = {}  # job_id -> {status, progress, step, error, path, filename, created_at, format}

def is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False
    return True

def validate_url(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Please enter a valid public http:// or https:// media URL.")
    if p.username or p.password:
        raise ValueError("Credentials inside URLs are not supported.")
    if not is_public_host(p.hostname):
        raise ValueError("Host is not publicly reachable or points to a private address.")
    return url

def extract_media_from_html(html, current_url):
    # 1. Suno AI link resolution
    if "suno.com" in current_url or "suno.ai" in current_url:
        m = re.search(r"/song/([0-9a-fA-F-]{10,})", html)
        if m:
            return f"https://cdn1.suno.ai/{m.group(1)}.mp3"
        m_cdn = re.search(r"https://cdn[0-9]?\.suno\.(?:ai|com)/[0-9a-fA-F-]{10,}\.mp3", html)
        if m_cdn:
            return m_cdn.group(0)

    # 2. General OpenGraph audio/video tags
    m_og = re.search(r'property=["\']og:audio(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if not m_og:
        m_og = re.search(r'content=["\']([^"\']+)["\'][^>]+property=["\']og:audio', html, re.I)
    if m_og:
        return m_og.group(1)

    return None

def resolve_special_url(url, session):
    p = urlparse(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    # Suno share link auto-resolver
    if "suno.com" in p.netloc or "suno.ai" in p.netloc:
        try:
            r = session.get(url, headers=headers, timeout=12, allow_redirects=True)
            # From final redirected URL (/song/<uuid>)
            m = re.search(r"/song/([0-9a-fA-F-]{10,})", r.url)
            if m:
                return f"https://cdn1.suno.ai/{m.group(1)}.mp3"
            # From HTML content
            extracted = extract_media_from_html(r.text, r.url)
            if extracted:
                return extracted
        except Exception:
            pass
    return url

def safe_download(url, target, job_id):
    session = requests.Session()
    resolved_initial = resolve_special_url(url, session)
    current = validate_url(resolved_initial)
    total = 0

    for _ in range(6):
        with session.get(current, stream=True, timeout=(10, 30), allow_redirects=False,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as r:
            if r.is_redirect or r.is_permanent_redirect:
                location = r.headers.get("Location")
                if not location:
                    raise ValueError("Source returned an invalid redirect.")
                current = validate_url(urljoin(current, location))
                continue
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()

            # If page is HTML, attempt to extract underlying audio link
            if any(x in ctype for x in ("text/html", "application/json", "text/plain")):
                content_sample = b"".join(r.iter_content(chunk_size=1024 * 64))[: 512 * 1024].decode("utf-8", errors="ignore")
                media_link = extract_media_from_html(content_sample, current)
                if media_link and media_link != current:
                    current = validate_url(media_link)
                    continue
                raise ValueError("The URL points to a webpage without direct media. Please use a direct media file or supported link.")

            content_length = r.headers.get("Content-Length")
            total_size = int(content_length) if content_length and content_length.isdigit() else 0

            with open(target, "wb") as f:
                for chunk in r.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError(f"File exceeds maximum allowed size ({MAX_BYTES // (1024*1024)}MB).")
                    f.write(chunk)
                    if total_size > 0:
                        pct = int((total / total_size) * 45)  # 0-45% for download
                        update_job(job_id, progress=pct, step="Downloading media file...")
            return
    raise ValueError("Too many redirects.")

def ffmpeg_convert(source, output, fmt, bitrate="192k", start_sec=None, end_sec=None, normalize=False):
    codecs = {
        "mp3": ["-codec:a", "libmp3lame", "-b:a", bitrate],
        "wav": ["-codec:a", "pcm_s16le"],
        "m4a": ["-codec:a", "aac", "-b:a", bitrate, "-f", "ipod"],
        "flac": ["-codec:a", "flac"],
        "ogg": ["-codec:a", "libvorbis", "-q:a", "5"],
        "aac": ["-codec:a", "aac", "-b:a", bitrate, "-f", "adts"],
    }

    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]

    if start_sec:
        cmd.extend(["-ss", str(start_sec)])
    if end_sec:
        cmd.extend(["-to", str(end_sec)])

    cmd.extend(["-i", str(source), "-vn"])

    filters = []
    if normalize:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if filters:
        cmd.extend(["-af", ",".join(filters)])

    cmd.extend(codecs.get(fmt, codecs["mp3"]))
    cmd.append(str(output))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        err = (result.stderr or "FFmpeg audio conversion failed.")[-500:]
        raise ValueError(err)

def update_job(job_id, **kwargs):
    with LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)

def process_worker(job_id, mode, data, fmt, bitrate, start_sec, end_sec, normalize):
    folder = BASE_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "source_media"
    output = folder / f"converted.{fmt}"

    with SEMAPHORE:
        try:
            update_job(job_id, status="processing", step="Starting media download...", progress=5)
            if mode == "url":
                safe_download(data["url"], source, job_id)
            elif mode == "file":
                update_job(job_id, progress=45, step="File uploaded successfully.")

            update_job(job_id, progress=60, step="Converting audio via FFmpeg...")
            ffmpeg_convert(source, output, fmt, bitrate, start_sec, end_sec, normalize)

            filename = f"converted-audio.{fmt}"
            if data.get("original_name"):
                base_name = Path(secure_filename(data["original_name"])).stem
                filename = f"{base_name}.{fmt}"

            update_job(job_id,
                       status="completed",
                       progress=100,
                       step="Conversion complete!",
                       path=str(output),
                       filename=filename,
                       download_url=f"/api/download/{job_id}",
                       preview_url=f"/api/preview/{job_id}")
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            update_job(job_id, status="failed", error=str(e)[:400], progress=0)

def background_cleanup_daemon():
    while True:
        time.sleep(60)
        now = time.time()
        to_delete = []
        with LOCK:
            for jid, info in list(JOBS.items()):
                # Delete jobs older than 15 minutes (900s)
                if now - info.get("created_at", now) > 900:
                    to_delete.append((jid, BASE_DIR / jid))
                    JOBS.pop(jid, None)
        for _, path in to_delete:
            shutil.rmtree(path, ignore_errors=True)

threading.Thread(target=background_cleanup_daemon, daemon=True).start()

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/convert")
def start_conversion():
    fmt = request.form.get("format", "mp3").lower()
    bitrate = request.form.get("bitrate", "192k").lower()
    start_sec = request.form.get("start_sec") or None
    end_sec = request.form.get("end_sec") or None
    normalize = request.form.get("normalize") == "true"

    if fmt not in ALLOWED_FORMATS:
        return jsonify(error="Unsupported format"), 400
    if bitrate not in ALLOWED_BITRATES:
        bitrate = "192k"

    job_id = uuid.uuid4().hex
    folder = BASE_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)

    mode = "url"
    payload = {}

    if "file" in request.files and request.files["file"].filename:
        uploaded_file = request.files["file"]
        mode = "file"
        source = folder / "source_media"
        uploaded_file.save(source)
        payload = {"original_name": uploaded_file.filename}
    else:
        url = (request.form.get("url") or "").strip()
        if not url:
            return jsonify(error="Provide a valid media URL or upload an audio/video file."), 400
        payload = {"url": url}

    with LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "step": "Queued in worker...",
            "created_at": time.time(),
            "format": fmt
        }

    threading.Thread(target=process_worker,
                     args=(job_id, mode, payload, fmt, bitrate, start_sec, end_sec, normalize),
                     daemon=True).start()

    return jsonify(job_id=job_id, status="queued")

@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify(error="Job not found or expired."), 404
    return jsonify(job)

@app.get("/api/preview/<job_id>")
def preview_audio(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="File not ready or expired"), 404
    path = Path(job["path"])
    if not path.exists():
        return jsonify(error="File missing"), 404
    return send_file(path, mimetype=f"audio/{job['format']}")

@app.get("/api/download/<job_id>")
def download_audio(job_id):
    with LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "completed":
        return jsonify(error="File not ready or expired"), 404
    path = Path(job["path"])
    if not path.exists():
        return jsonify(error="File missing"), 404
    return send_file(path, as_attachment=True, download_name=job.get("filename", f"audio.{job['format']}"))

@app.get("/health")
def health():
    return jsonify(status="ok", active_jobs=len(JOBS))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
