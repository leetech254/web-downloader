"""A small, safer web front-end for yt-dlp."""

from __future__ import annotations

import ipaddress
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import uuid
from urllib.parse import urlsplit

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wsgi import ClosingIterator

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")

SOCKET_TIMEOUT = max(5, int(os.getenv("SOCKET_TIMEOUT", "30")))
MAX_DOWNLOAD_BYTES = max(1, int(os.getenv("MAX_DOWNLOAD_BYTES", str(1024**3))))
MAX_DURATION_SECONDS = max(60, int(os.getenv("MAX_DURATION_SECONDS", str(4 * 60 * 60))))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "2")))
MAX_COOKIE_BYTES = max(1024, int(os.getenv("MAX_COOKIE_BYTES", str(2 * 1024**2))))
COOKIE_AUTH_ENABLED = os.getenv("ENABLE_COOKIE_AUTH", "0").strip().lower() in {"1", "true", "yes"}
ENABLE_BROWSER_COOKIES = os.getenv("ENABLE_BROWSER_COOKIES", "0").strip().lower() in {"1", "true", "yes"}
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0").strip().lower() in {"1", "true", "yes"}
app.config["MAX_CONTENT_LENGTH"] = MAX_COOKIE_BYTES + 64 * 1024
if TRUST_PROXY_HEADERS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)

VIDEO_QUALITIES = {"best", "2160", "1440", "1080", "720", "480", "360"}
AUDIO_QUALITIES = {"320", "256", "192", "128"}
BROWSER_COOKIE_SOURCES = ("brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale")
BROWSER_KEYRINGS = ("basictext", "gnomekeyring", "kwallet", "kwallet5", "kwallet6")
PLATFORM_EXAMPLES = (
    ("YouTube", "youtube"), ("Instagram", "instagram"), ("TikTok", "tiktok"),
    ("Facebook", "facebook"), ("X", "twitter"), ("Reddit", "reddit"),
    ("Twitch", "twitch"), ("Vimeo", "vimeo"), ("SoundCloud", "soundcloud"),
    ("Pinterest", "pinterest"), ("Snapchat Spotlight", "snapchatspotlight"),
    ("Bluesky", "bluesky"), ("Bilibili", "bilibili"), ("Rumble", "rumble"),
    ("Dailymotion", "dailymotion"), ("VK", "vk"), ("LinkedIn", "linkedin"),
    ("Tumblr", "tumblr"), ("Weibo", "weibo"), ("Kick", "kick"),
    ("Telegram embeds", "telegramembed"),
)
_EXTRACTOR_KEYS = {extractor.ie_key().casefold() for extractor in gen_extractor_classes()}
SIDECAR_EXTENSIONS = {
    ".webp", ".jpg", ".jpeg", ".png", ".gif", ".json", ".description",
    ".vtt", ".srt", ".ass", ".lrc", ".part", ".ytdl", ".temp",
}


class InputError(ValueError):
    """A safe, user-correctable request error."""


def validate_url(value: str) -> str:
    """Validate a public HTTP(S) media URL and reject obvious SSRF targets."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise InputError("Enter a valid media URL (up to 2,048 characters).")

    value = value.strip()
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise InputError("The URL is malformed.") from exc

    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise InputError("Use a complete HTTP or HTTPS media URL.")
    if parsed.username is not None or parsed.password is not None:
        raise InputError("URLs containing embedded usernames or passwords are not accepted.")
    if port is not None and not 1 <= port <= 65535:
        raise InputError("The URL port is invalid.")

    host = hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise InputError("Local and private network URLs are not accepted.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        if not address.is_global:
            raise InputError("Local and private network URLs are not accepted.")
    else:
        try:
            answers = socket.getaddrinfo(host, port or (443 if parsed.scheme.lower() == "https" else 80), type=socket.SOCK_STREAM)
        except (OSError, socket.gaierror) as exc:
            raise InputError("The URL host could not be resolved.") from exc
        addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
        if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
            raise InputError("The URL host must resolve only to public internet addresses.")

    return value


def _options(format_type: str, quality: str, output_template: str) -> dict:
    if format_type == "original":
        return {
            "format": "best/bestvideo+bestaudio",
            "outtmpl": output_template,
        }

    if format_type == "mp3":
        if quality not in AUDIO_QUALITIES:
            raise InputError("Choose a supported MP3 bitrate.")
        return {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            }, {"key": "FFmpegMetadata"}],
        }

    if format_type != "mp4":
        raise InputError("Choose MP4 video or MP3 audio.")
    if quality not in VIDEO_QUALITIES:
        raise InputError("Choose a supported video quality.")

    if quality == "best":
        fmt = "bestvideo+bestaudio/best"
    else:
        fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
    return {
        "format": fmt,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
    }


def _browser_cookie_options() -> tuple | None:
    browser = request.form.get("browser_cookie_browser", "").strip().lower()
    if not browser:
        return None
    if not ENABLE_BROWSER_COOKIES:
        raise InputError("Browser-profile cookie access is disabled on this server.")
    if browser not in BROWSER_COOKIE_SOURCES:
        raise InputError("Choose a supported browser profile source.")
    if request.form.get("browser_cookie_ack") != "1":
        raise InputError("Confirm local browser-profile access before continuing.")
    uploaded_cookie_file = request.files.get("cookies")
    if uploaded_cookie_file is not None and uploaded_cookie_file.filename:
        raise InputError("Choose either a browser profile or a cookie file, not both.")
    if TRUST_PROXY_HEADERS:
        raise InputError("Browser-profile access is unavailable when proxy headers are trusted.")

    try:
        remote_is_loopback = ipaddress.ip_address(request.remote_addr or "").is_loopback
        request_host = urlsplit(f"{request.scheme}://{request.host}").hostname
    except ValueError:
        remote_is_loopback = False
        request_host = None
    if not remote_is_loopback or request_host not in {"localhost", "127.0.0.1", "::1"}:
        raise InputError("Browser-profile access is available only from this machine through localhost.")

    origin = request.headers.get("Origin", "")
    try:
        parsed_origin = urlsplit(origin)
    except ValueError as exc:
        raise InputError("Browser-profile access requires a same-origin request.") from exc
    if (
        parsed_origin.scheme != request.scheme
        or parsed_origin.netloc.casefold() != request.host.casefold()
        or parsed_origin.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise InputError("Browser-profile access requires a same-origin localhost request.")

    supplied_token = request.form.get("csrf_token", "")
    expected_token = session.get("csrf_token", "")
    if not expected_token or not secrets.compare_digest(supplied_token, expected_token):
        raise InputError("Browser-profile access request failed its security check. Reload the page and retry.")

    profile = request.form.get("browser_cookie_profile", "").strip()
    if profile and (len(profile) > 80 or ".." in profile or not re.fullmatch(r"[A-Za-z0-9 _.-]+", profile)):
        raise InputError("Enter a profile name only; filesystem paths are not accepted.")
    keyring = request.form.get("browser_cookie_keyring", "").strip().lower()
    if keyring and keyring not in BROWSER_KEYRINGS:
        raise InputError("Choose a supported browser keyring option.")

    return (browser, profile or None, keyring or None, None)


def _store_cookie_upload(temp_dir: str) -> str | None:
    uploaded = request.files.get("cookies")
    if uploaded is None or not uploaded.filename:
        return None
    if not COOKIE_AUTH_ENABLED:
        raise InputError("Cookie authentication is disabled on this server.")
    if request.form.get("cookie_ack") != "1":
        raise InputError("Confirm that you trust this server before uploading account cookies.")
    try:
        loopback = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        loopback = False
    if not request.is_secure and not loopback:
        raise InputError("Cookie files may be uploaded only over HTTPS or from localhost.")

    content = uploaded.stream.read(MAX_COOKIE_BYTES + 1)
    if len(content) > MAX_COOKIE_BYTES:
        raise InputError(f"The cookie file exceeds the {MAX_COOKIE_BYTES // 1024} KB limit.")
    if not content or b"\x00" in content:
        raise InputError("Upload a valid Netscape-format cookie text file.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InputError("The cookie file must be UTF-8 Netscape-format text.") from exc

    lines = text.splitlines()
    header = next((line.strip() for line in lines if line.strip()), "")
    if header not in {"# Netscape HTTP Cookie File", "# HTTP Cookie File"}:
        raise InputError("The cookie file must start with a Netscape HTTP Cookie File header.")

    cookie_count = 0
    for line in lines:
        if not line.strip() or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        fields = line.split("\t")
        if len(fields) != 7 or not fields[0] or fields[1].upper() not in {"TRUE", "FALSE"}:
            raise InputError("The cookie file contains an invalid Netscape cookie row.")
        if fields[3].upper() not in {"TRUE", "FALSE"} or not fields[5]:
            raise InputError("The cookie file contains an invalid Netscape cookie row.")
        if fields[4] and not fields[4].lstrip("-").isdigit():
            raise InputError("The cookie file contains an invalid expiration value.")
        cookie_count += 1
    if cookie_count == 0:
        raise InputError("The cookie file contains no cookies.")

    cookie_path = os.path.join(temp_dir, "yt-dlp-cookies.txt")
    descriptor = os.open(cookie_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as cookie_file:
        cookie_file.write(content)
    return cookie_path


def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": False,
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 2,
        "file_access_retries": 2,
        "concurrent_fragment_downloads": 4,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "cachedir": False,
    }


def _duration(info: dict) -> int | None:
    value = info.get("duration")
    try:
        return max(0, int(value)) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_title(value: object) -> str:
    title = str(value or "media")
    title = "".join(char for char in title if char.isalnum() or char in " -_.()[]")
    title = re.sub(r"\s+", " ", title).strip(" .")[:160]
    return title or "media"


def _user_error(error: Exception) -> str:
    """Map extractor failures to useful messages without leaking internals."""
    text = str(error).lower()
    if "unsupported url" in text or "not a valid url" in text:
        return "This link is not supported by the extractor. Try a direct public post or video link."
    if any(term in text for term in ("private video", "login", "authentication", "age-restricted", "age restricted", "confirm your age", "sign in to confirm")):
        return "This media requires sign-in or could not be authenticated. Check that your optional cookie file or authorized local browser profile is valid and current."
    if "geo restricted" in text or "not available in your country" in text:
        return "This media is not available from this server's location."
    if "ffmpeg" in text or "postprocessing" in text:
        return "Media conversion failed. Please try another format or contact the site administrator."
    if "file is larger than" in text or "max_filesize" in text:
        return "This file exceeds the server's configured download-size limit."
    return "Could not process this media. The link may be unavailable, unsupported, or temporarily blocked."


def _acquire_slot():
    if not _JOB_SLOTS.acquire(blocking=False):
        return False
    return True


def _download_error(message: str, status: int):
    if "text/html" in request.headers.get("Accept", ""):
        return render_template("error.html", error=message), status
    return jsonify(error=message), status


def _extract_info(url: str, options: dict) -> dict:
    with yt_dlp.YoutubeDL({**_base_options(), **options}) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
        raise InputError("Playlist and collection URLs are not supported; use a single media item.")
    duration = _duration(info)
    if duration is not None and duration > MAX_DURATION_SECONDS:
        raise InputError(f"This media is longer than the {MAX_DURATION_SECONDS // 3600}-hour server limit.")
    return info


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
        "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    return response


@app.get("/")
def index():
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template(
        "index.html",
        cookie_auth_enabled=COOKIE_AUTH_ENABLED,
        max_cookie_bytes=MAX_COOKIE_BYTES,
        browser_cookies_enabled=ENABLE_BROWSER_COOKIES,
        browser_cookie_sources=BROWSER_COOKIE_SOURCES,
        csrf_token=csrf_token,
    )


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/api/platforms")
def api_platforms():
    examples = [name for name, extractor_key in PLATFORM_EXAMPLES if extractor_key in _EXTRACTOR_KEYS]
    return jsonify(
        examples=examples,
        note="Examples only. yt-dlp also includes many other extractors; support depends on each URL and the source site's current access rules.",
    )


@app.post("/api/info")
def api_info():
    if not _acquire_slot():
        return jsonify(error="The downloader is busy. Please try again shortly."), 429
    temp_dir = None
    try:
        url = validate_url(request.form.get("url", ""))
        browser_cookies = _browser_cookie_options()
        temp_dir = tempfile.mkdtemp(prefix="web-downloader-preview-")
        cookie_path = _store_cookie_upload(temp_dir)
        options = _options(request.form.get("format", "mp4"), request.form.get("quality", "1080"), "%(id)s.%(ext)s")
        if cookie_path:
            options["cookiefile"] = cookie_path
        if browser_cookies:
            options["cookiesfrombrowser"] = browser_cookies
        info = _extract_info(url, options)
        thumb = info.get("thumbnail")
        if not isinstance(thumb, str) or not thumb.startswith("https://"):
            thumb = None
        return jsonify(
            title=_safe_title(info.get("title")),
            uploader=_safe_title(info.get("uploader") or info.get("channel") or ""),
            duration=_duration(info),
            thumbnail=thumb,
            extractor=_safe_title(info.get("extractor_key") or info.get("extractor") or "Media site"),
        )
    except InputError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:  # The details stay in server logs, not in the response.
        app.logger.warning("Metadata extraction failed (%s)", type(exc).__name__)
        return jsonify(error=_user_error(exc)), 502
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        _JOB_SLOTS.release()


@app.post("/download")
def download():
    if not _acquire_slot():
        return _download_error("The downloader is busy. Please try again shortly.", 429)

    temp_dir = None
    try:
        url = validate_url(request.form.get("url", ""))
        browser_cookies = _browser_cookie_options()
        format_type = request.form.get("format", "mp4")
        quality = request.form.get("quality", "1080")
        temp_dir = tempfile.mkdtemp(prefix="web-downloader-")
        cookie_path = _store_cookie_upload(temp_dir)
        unique_name = uuid.uuid4().hex
        output_template = os.path.join(temp_dir, unique_name + ".%(ext)s")
        format_options = _options(format_type, quality, output_template)
        if cookie_path:
            format_options["cookiefile"] = cookie_path
        if browser_cookies:
            format_options["cookiesfrombrowser"] = browser_cookies
        options = {**_base_options(), **format_options}

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
                raise InputError("Playlist and collection URLs are not supported; use a single media item.")
            duration = _duration(info)
            if duration is not None and duration > MAX_DURATION_SECONDS:
                raise InputError(f"This media is longer than the {MAX_DURATION_SECONDS // 3600}-hour server limit.")
            ydl.process_ie_result(info, download=True)

        candidates = []
        for filename in os.listdir(temp_dir):
            path = os.path.join(temp_dir, filename)
            extension = os.path.splitext(filename)[1].lower()
            if filename.startswith(unique_name) and os.path.isfile(path) and extension not in SIDECAR_EXTENSIONS:
                candidates.append(path)
        if not candidates:
            raise RuntimeError("No media file was produced")
        media_path = max(candidates, key=os.path.getsize)
        if os.path.getsize(media_path) > MAX_DOWNLOAD_BYTES:
            raise InputError("This file exceeds the server's configured download-size limit.")

        extension = os.path.splitext(media_path)[1].lstrip(".").lower()
        if not re.fullmatch(r"[a-z0-9]{1,8}", extension):
            extension = "bin"
        response = send_file(
            media_path,
            as_attachment=True,
            download_name=f"{_safe_title(info.get('title'))}.{extension}",
            conditional=True,
            max_age=0,
        )
        response.response = ClosingIterator(
            response.response,
            lambda directory=temp_dir: shutil.rmtree(directory, ignore_errors=True),
        )
        temp_dir = None  # Response callback owns cleanup now.
        return response
    except InputError as exc:
        return _download_error(str(exc), 400)
    except Exception as exc:
        app.logger.warning("Download failed (%s)", type(exc).__name__)
        return _download_error(_user_error(exc), 502)
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        _JOB_SLOTS.release()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
