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
import time
import uuid
from collections import deque
from math import ceil
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

import yt_dlp
from yt_dlp.extractor import gen_extractor_classes
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wsgi import ClosingIterator

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict")

DEFAULT_PUBLIC_SITE_URL = "https://mediagrab-kbljqk2v.manus.space"


def _configured_public_site_url() -> str:
    """Return an explicitly configured public origin, never a request-derived host."""
    value = os.getenv("PUBLIC_SITE_URL", DEFAULT_PUBLIC_SITE_URL).strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PUBLIC_SITE_URL must be an absolute HTTP(S) site origin without a path, query, or fragment.")
    return f"{parsed.scheme}://{parsed.netloc}"


PUBLIC_SITE_URL = _configured_public_site_url()

SOCKET_TIMEOUT = max(5, int(os.getenv("SOCKET_TIMEOUT", "30")))
MAX_DOWNLOAD_BYTES = max(1, int(os.getenv("MAX_DOWNLOAD_BYTES", str(1024**3))))
MAX_DURATION_SECONDS = max(60, int(os.getenv("MAX_DURATION_SECONDS", str(4 * 60 * 60))))
MAX_CONCURRENT_JOBS = max(1, int(os.getenv("MAX_CONCURRENT_JOBS", "2")))
PREVIEW_LIMIT_PER_MINUTE = max(1, int(os.getenv("PREVIEW_LIMIT_PER_MINUTE", "10")))
PREVIEW_LIMIT_PER_HOUR = max(1, int(os.getenv("PREVIEW_LIMIT_PER_HOUR", "30")))
DOWNLOAD_LIMIT_PER_HOUR = max(1, int(os.getenv("DOWNLOAD_LIMIT_PER_HOUR", "3")))
DOWNLOAD_LIMIT_PER_DAY = max(1, int(os.getenv("DOWNLOAD_LIMIT_PER_DAY", "10")))
MAX_RATE_LIMIT_BUCKETS = max(100, int(os.getenv("MAX_RATE_LIMIT_BUCKETS", "10000")))
MAX_COOKIE_BYTES = max(1024, int(os.getenv("MAX_COOKIE_BYTES", str(2 * 1024**2))))
COOKIE_AUTH_ENABLED = os.getenv("ENABLE_COOKIE_AUTH", "0").strip().lower() in {"1", "true", "yes"}
ENABLE_BROWSER_COOKIES = os.getenv("ENABLE_BROWSER_COOKIES", "0").strip().lower() in {"1", "true", "yes"}
TRUST_PROXY_HEADERS = os.getenv("TRUST_PROXY_HEADERS", "0").strip().lower() in {"1", "true", "yes"}
app.config["MAX_CONTENT_LENGTH"] = MAX_COOKIE_BYTES + 64 * 1024
if TRUST_PROXY_HEADERS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_BUCKETS: dict[tuple[str, str], tuple[deque[float], int]] = {}
_RATE_LIMIT_LAST_SWEEP = time.monotonic()

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
PLATFORM_EXAMPLES_NOTE = "Examples only. yt-dlp also includes many other extractors; support depends on each URL and the source site's current access rules."


def _platform_catalog() -> tuple[list[str], str]:
    examples = [name for name, extractor_key in PLATFORM_EXAMPLES if extractor_key in _EXTRACTOR_KEYS]
    return examples, PLATFORM_EXAMPLES_NOTE


SIDECAR_EXTENSIONS = {
    ".webp", ".jpg", ".jpeg", ".png", ".gif", ".json", ".description",
    ".vtt", ".srt", ".ass", ".lrc", ".part", ".ytdl", ".temp",
}
IMAGE_EXTENSIONS = {"apng", "avif", "bmp", "gif", "heic", "heif", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
VALID_FORMATS = {"auto", "mp4", "mp3", "original"}


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


def _validate_quality(format_type: str, quality: str) -> None:
    if format_type == "mp3" and quality not in AUDIO_QUALITIES:
        raise InputError("Choose a supported MP3 bitrate.")
    if format_type == "mp4" and quality not in VIDEO_QUALITIES:
        raise InputError("Choose a supported video quality.")


def _options(format_type: str, quality: str, output_template: str) -> dict:
    _validate_quality(format_type, quality)
    if format_type == "auto":
        # Let yt-dlp choose a compatible format from the source without forcing conversion.
        return {"outtmpl": output_template}

    if format_type == "original":
        return {
            "format": "best/bestvideo+bestaudio",
            "outtmpl": output_template,
        }

    if format_type == "mp3":
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


def _stream_availability(info: dict, codec_key: str) -> bool | None:
    """Return whether a stream exists, or None when the extractor gives no codec evidence."""
    formats = info.get("formats")
    candidates = [info, *(formats if isinstance(formats, list) else [])]
    codec_values = [item.get(codec_key) for item in candidates if isinstance(item, dict) and codec_key in item]
    if any(value and str(value).casefold() not in {"none", "unknown"} for value in codec_values):
        return True
    return False if codec_values else None


def _media_type(info: dict) -> str:
    """Classify extractor metadata without tying detection to the requested output format."""
    if _stream_availability(info, "vcodec") is True:
        return "video"
    if _stream_availability(info, "acodec") is True:
        return "audio"
    candidates = [info]
    formats = info.get("formats")
    if isinstance(formats, list):
        candidates.extend(formats)
    extensions = {
        str(item.get("ext") or "").casefold().lstrip(".")
        for item in candidates if isinstance(item, dict)
    }
    if extensions & IMAGE_EXTENSIONS:
        return "image"
    return "unknown"


def _validate_format_for_media(format_type: str, media_type: str, has_audio: bool | None) -> None:
    if format_type == "mp4" and media_type == "audio":
        raise InputError("This link contains audio-only media. Choose Auto or MP3 instead of MP4.")
    if format_type in {"mp4", "mp3"} and media_type == "image":
        raise InputError("This link contains images, not audio or video. Choose Auto to keep the original format.")
    if format_type == "mp3" and media_type == "video" and has_audio is False:
        raise InputError("This video has no detected audio track. Choose Auto or MP4 instead of MP3.")


def _safe_title(value: object) -> str:
    title = str(value or "media")
    title = "".join(char for char in title if char.isalnum() or char in " -_.()[]")
    title = re.sub(r"\s+", " ", title).strip(" .")[:160]
    return title or "media"


def _user_error(error: Exception) -> str:
    """Map extractor failures to useful messages without leaking internals."""
    text = str(error).lower()
    if "unsupported url" in text or "not a valid url" in text:
        return "We couldn't recognize this link. Check that it points to one public post or media item, then try again."
    if any(term in text for term in ("private video", "login", "authentication", "age-restricted", "age restricted", "confirm your age", "sign in to confirm")):
        if COOKIE_AUTH_ENABLED:
            return "This media requires sign-in or age verification. Only use your own authorized account cookies on a server you trust."
        return "This media appears to require sign-in or age verification. This public service cannot use account cookies; try a public link instead."
    if "http error 429" in text or "too many requests" in text or "rate limit" in text:
        return "The source site is temporarily limiting requests. Wait a while, then try again."
    if "http error 403" in text or "403 forbidden" in text:
        return "The source site refused this request. The post may be restricted, or the site may be blocking automated access."
    if "geo restricted" in text or "not available in your country" in text:
        return "This media is not available from this server's location."
    if "ffmpeg" in text or "postprocessing" in text:
        return "Media conversion failed. Please try another format or contact the site administrator."
    if "file is larger than" in text or "max_filesize" in text:
        limit_mib = max(1, ceil(MAX_DOWNLOAD_BYTES / (1024 * 1024)))
        return f"This file is larger than this server's configured limit of {limit_mib} MiB. Try a lower video quality or a shorter item."
    if "timed out" in text or "timeout" in text or any(term in text for term in ("connection reset", "connection refused", "temporary failure in name resolution", "name or service not known", "network is unreachable")):
        return "The source site could not be reached in time. Check the link and try again later."
    if "http error 404" in text or "video unavailable" in text or "not available" in text:
        return "The media could not be found. It may have been removed, made private, or the link may be incomplete."
    return "Could not process this media. The link may be unavailable, unsupported, or temporarily blocked."


def _acquire_slot():
    if not _JOB_SLOTS.acquire(blocking=False):
        return False
    return True


def _enforce_client_rate_limit(action: str) -> int | None:
    """Return the retry delay when a client has exhausted a bounded sliding window."""
    policies = {
        "preview": (("preview_minute", PREVIEW_LIMIT_PER_MINUTE, 60),
                    ("preview_hour", PREVIEW_LIMIT_PER_HOUR, 3600)),
        "download": (("download_hour", DOWNLOAD_LIMIT_PER_HOUR, 3600),
                     ("download_day", DOWNLOAD_LIMIT_PER_DAY, 86400)),
    }.get(action)
    if policies is None:
        raise ValueError("Unknown rate-limit action")

    try:
        client_ip = ipaddress.ip_address(request.remote_addr or "").compressed
    except ValueError:
        client_ip = "unknown"

    global _RATE_LIMIT_LAST_SWEEP
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        if now - _RATE_LIMIT_LAST_SWEEP >= 60:
            for bucket_key, (events, window) in list(_RATE_LIMIT_BUCKETS.items()):
                while events and now - events[0] >= window:
                    events.popleft()
                if not events:
                    _RATE_LIMIT_BUCKETS.pop(bucket_key, None)
            _RATE_LIMIT_LAST_SWEEP = now

        active_buckets = []
        missing_keys = 0
        for name, limit, window in policies:
            key = (client_ip, name)
            bucket = _RATE_LIMIT_BUCKETS.get(key)
            if bucket is None:
                missing_keys += 1
                active_buckets.append((key, limit, window, None))
                continue
            events, stored_window = bucket
            while events and now - events[0] >= stored_window:
                events.popleft()
            if not events:
                _RATE_LIMIT_BUCKETS.pop(key, None)
                missing_keys += 1
                active_buckets.append((key, limit, window, None))
            else:
                active_buckets.append((key, limit, stored_window, events))

        if len(_RATE_LIMIT_BUCKETS) + missing_keys > MAX_RATE_LIMIT_BUCKETS:
            return max(1, max(window for _, _, window in policies))

        retry_after = 0
        for _, limit, window, events in active_buckets:
            if events is not None and len(events) >= limit:
                retry_after = max(retry_after, ceil(window - (now - events[0])))
        if retry_after:
            return max(1, retry_after)

        for key, _, window, events in active_buckets:
            if events is None:
                events = deque()
                _RATE_LIMIT_BUCKETS[key] = (events, window)
            events.append(now)
    return None


def _download_error(message: str, status: int, *, code: str = "request_failed", retry_after: int | None = None):
    accept = request.headers.get("Accept", "")
    if "application/json" in accept or "text/html" not in accept:
        payload = {"error": message, "code": code}
        if retry_after is not None:
            payload["retry_after"] = max(1, retry_after)
        response = app.make_response((jsonify(payload), status))
    else:
        response = app.make_response((render_template("error.html", error=message, code=code), status))
    if retry_after is not None:
        response.headers["Retry-After"] = str(max(1, retry_after))
    return response


def _rate_limit_response(message: str, retry_after: int):
    return _download_error(message, 429, code="rate_limited", retry_after=retry_after)


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
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
        "connect-src 'self'; base-uri 'self'; form-action 'self'",
    )
    if request.endpoint not in {"index", "robots_txt", "sitemap_xml", "static"} or response.status_code >= 400:
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return response


@app.context_processor
def inject_public_metadata_urls():
    """Provide absolute crawler URLs only when a public origin is configured."""
    return {
        "public_site_url": PUBLIC_SITE_URL,
        "canonical_url": f"{PUBLIC_SITE_URL}/" if PUBLIC_SITE_URL else "",
        "social_image_url": f"{PUBLIC_SITE_URL}/static/social-preview.jpg" if PUBLIC_SITE_URL else "",
    }


@app.errorhandler(413)
def request_too_large(_error):
    return _download_error(
        "This request is too large. Remove the cookie file or choose a smaller upload.",
        413,
        code="request_too_large",
    )


@app.get("/")
def index():
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    platform_examples, platform_note = _platform_catalog()
    return render_template(
        "index.html",
        cookie_auth_enabled=COOKIE_AUTH_ENABLED,
        max_cookie_bytes=MAX_COOKIE_BYTES,
        max_download_bytes=MAX_DOWNLOAD_BYTES,
        browser_cookies_enabled=ENABLE_BROWSER_COOKIES,
        browser_cookie_sources=BROWSER_COOKIE_SOURCES,
        csrf_token=csrf_token,
        platform_examples=platform_examples,
        platform_note=platform_note,
    )


@app.get("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /download",
        "Disallow: /healthz",
        "Disallow: /manus-routes.json",
    ]
    if PUBLIC_SITE_URL:
        lines.append(f"Sitemap: {PUBLIC_SITE_URL}/sitemap.xml")
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    if not PUBLIC_SITE_URL:
        return Response(
            "Configure PUBLIC_SITE_URL to publish an absolute sitemap location.\n",
            status=503,
            mimetype="text/plain",
        )
    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    url = SubElement(urlset, "url")
    SubElement(url, "loc").text = f"{PUBLIC_SITE_URL}/"
    xml = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(urlset, encoding="utf-8")
    return Response(xml, mimetype="application/xml")


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/manus-routes.json")
def manus_routes():
    return send_from_directory(os.path.join(app.root_path, "public"), "manus-routes.json")


@app.get("/api/platforms")
def api_platforms():
    examples, note = _platform_catalog()
    return jsonify(examples=examples, note=note)


@app.post("/api/info")
def api_info():
    retry_after = _enforce_client_rate_limit("preview")
    if retry_after is not None:
        return _rate_limit_response(
            f"Preview limit reached. Try again in {retry_after} seconds.", retry_after
        )
    if not _acquire_slot():
        return _download_error(
            "The downloader is busy. Please wait a few seconds and try again.",
            429,
            code="busy",
            retry_after=15,
        )
    temp_dir = None
    try:
        url = validate_url(request.form.get("url", ""))
        browser_cookies = _browser_cookie_options()
        temp_dir = tempfile.mkdtemp(prefix="web-downloader-preview-")
        cookie_path = _store_cookie_upload(temp_dir)
        # Preview all source formats first so an output choice cannot prevent detection.
        options = {}
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
            media_type=_media_type(info),
            has_audio=_stream_availability(info, "acodec"),
        )
    except RequestEntityTooLarge:
        return _download_error(
            "This request is too large. Remove the cookie file or choose a smaller upload.",
            413,
            code="request_too_large",
        )
    except InputError as exc:
        return jsonify(error=str(exc), code="invalid_request"), 400
    except Exception as exc:  # The details stay in server logs, not in the response.
        app.logger.warning("Metadata extraction failed (%s)", type(exc).__name__)
        return jsonify(error=_user_error(exc), code="source_error"), 502
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        _JOB_SLOTS.release()


@app.post("/download")
def download():
    retry_after = _enforce_client_rate_limit("download")
    if retry_after is not None:
        return _rate_limit_response(
            f"Download limit reached. Try again in {retry_after} seconds.", retry_after
        )
    if not _acquire_slot():
        return _download_error(
            "The downloader is busy. Please wait a few seconds and try again.",
            429,
            code="busy",
            retry_after=15,
        )

    temp_dir = None
    try:
        url = validate_url(request.form.get("url", ""))
        browser_cookies = _browser_cookie_options()
        format_type = request.form.get("format", "auto")
        quality = request.form.get("quality", "1080")
        if format_type not in VALID_FORMATS:
            raise InputError("Choose Auto, MP4 video, MP3 audio, or original format.")
        _validate_quality(format_type, quality)
        temp_dir = tempfile.mkdtemp(prefix="web-downloader-")
        cookie_path = _store_cookie_upload(temp_dir)
        unique_name = uuid.uuid4().hex
        output_template = os.path.join(temp_dir, unique_name + ".%(ext)s")
        extract_options = _base_options()
        if cookie_path:
            extract_options["cookiefile"] = cookie_path
        if browser_cookies:
            extract_options["cookiesfrombrowser"] = browser_cookies

        with yt_dlp.YoutubeDL(extract_options) as extractor:
            info = extractor.extract_info(url, download=False)
        if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}:
            raise InputError("Playlist and collection URLs are not supported; use a single media item.")
        duration = _duration(info)
        if duration is not None and duration > MAX_DURATION_SECONDS:
            raise InputError(f"This media is longer than the {MAX_DURATION_SECONDS // 3600}-hour server limit.")
        media_type = _media_type(info)
        has_audio = _stream_availability(info, "acodec")
        _validate_format_for_media(format_type, media_type, has_audio)
        format_options = _options(format_type, quality, output_template)
        if cookie_path:
            format_options["cookiefile"] = cookie_path
        if browser_cookies:
            format_options["cookiesfrombrowser"] = browser_cookies
        with yt_dlp.YoutubeDL({**_base_options(), **format_options}) as ydl:
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
    except RequestEntityTooLarge:
        return _download_error(
            "This request is too large. Remove the cookie file or choose a smaller upload.",
            413,
            code="request_too_large",
        )
    except InputError as exc:
        return _download_error(str(exc), 400, code="invalid_request")
    except Exception as exc:
        app.logger.warning("Download failed (%s)", type(exc).__name__)
        return _download_error(_user_error(exc), 502, code="source_error")
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        _JOB_SLOTS.release()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
