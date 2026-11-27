import os
import stat
import tempfile
import unittest
from xml.etree.ElementTree import fromstring
from io import BytesIO
from unittest.mock import patch

import app as downloader


class FakeYoutubeDL:
    cookie_path = None
    cookie_mode = None
    browser_options = None
    last_options = None

    def __init__(self, options):
        self.options = options
        FakeYoutubeDL.last_options = options
        FakeYoutubeDL.browser_options = options.get("cookiesfrombrowser")
        cookie_path = options.get("cookiefile")
        if cookie_path:
            FakeYoutubeDL.cookie_path = cookie_path
            FakeYoutubeDL.cookie_mode = stat.S_IMODE(os.stat(cookie_path).st_mode)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        return {
            "id": "sample",
            "title": "Sample media / safe title",
            "uploader": "Example channel",
            "extractor_key": "Example",
            "duration": 123,
            "thumbnail": "https://cdn.example.test/thumb.jpg",
            "ext": "mp4",
            "formats": [{"format_id": "video", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"}],
            "_type": "video",
        }

    def process_ie_result(self, info, download=False):
        target = self.options["outtmpl"].replace("%(ext)s", "mp4")
        with open(target, "wb") as media:
            media.write(b"sample media bytes")
        return info


class FailingYoutubeDL(FakeYoutubeDL):
    failure_message = "HTTP Error 429: Too Many Requests"

    def extract_info(self, url, download=False):
        raise RuntimeError(self.failure_message)


class LongYoutubeDL(FakeYoutubeDL):
    def extract_info(self, url, download=False):
        info = super().extract_info(url, download)
        info["duration"] = 123
        return info


class AudioOnlyYoutubeDL(FakeYoutubeDL):
    def extract_info(self, url, download=False):
        info = super().extract_info(url, download)
        info["ext"] = "m4a"
        info["formats"] = [{"format_id": "audio", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"}]
        return info


class ImageYoutubeDL(FakeYoutubeDL):
    def extract_info(self, url, download=False):
        info = super().extract_info(url, download)
        info["ext"] = "jpg"
        info["formats"] = [{"format_id": "image", "ext": "jpg", "vcodec": "none", "acodec": "none"}]
        return info


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        downloader.app.config.update(TESTING=True)
        self.client = downloader.app.test_client()
        FakeYoutubeDL.cookie_path = None
        FakeYoutubeDL.cookie_mode = None
        FakeYoutubeDL.browser_options = None
        FakeYoutubeDL.last_options = None
        with downloader._RATE_LIMIT_LOCK:
            downloader._RATE_LIMIT_BUCKETS.clear()
            downloader._RATE_LIMIT_LAST_SWEEP = downloader.time.monotonic()

    def public_dns(self, *args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 443))]

    def test_rejects_non_http_and_embedded_credentials(self):
        for value in ("ftp://example.com/file", "https://user:pass@example.com/file", "not a url"):
            with self.subTest(value=value), self.assertRaises(downloader.InputError):
                downloader.validate_url(value)

    def test_rejects_local_and_private_targets(self):
        for value in ("http://localhost/a", "http://127.0.0.1/a", "http://10.0.0.2/a", "http://service.local/a"):
            with self.subTest(value=value), self.assertRaises(downloader.InputError):
                downloader.validate_url(value)

    @patch("app.socket.getaddrinfo")
    def test_accepts_public_host(self, resolver):
        resolver.side_effect = self.public_dns
        self.assertEqual(downloader.validate_url("https://example.com/watch?v=1"), "https://example.com/watch?v=1")

    @patch("app.socket.getaddrinfo")
    def test_rejects_host_resolving_to_private_address(self, resolver):
        resolver.return_value = [(None, None, None, None, ("192.168.1.8", 443))]
        with self.assertRaises(downloader.InputError):
            downloader.validate_url("https://example.com/watch")

    def test_health_endpoint_and_security_headers(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("X-Frame-Options", response.headers)
        self.assertNotIn("frame-ancestors", response.headers["Content-Security-Policy"])

    def test_homepage_exposes_transfer_progress_and_memory_limit(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="download-progress"', response.data)
        self.assertIn(b'id="download-progress-label"', response.data)
        self.assertIn(b"temporarily buffers the download in browser memory", response.data)
        self.assertIn(b'value="auto" checked', response.data)
        self.assertIn(b'by <strong>Lee Tech</strong>', response.data)
        self.assertIn(b'href="/docs">Documentation', response.data)
        self.assertIn(b'href="/">Open downloader', response.data)
        self.assertNotIn(b"Source code", response.data)
        self.assertNotIn(b"yt-dlp project", response.data)
        self.assertIn(b"Downloads are processed temporarily and removed after transfer.", response.data)
        self.assertNotIn(b"Browser-profile cookie access is disabled by the server.", response.data)
        self.assertNotIn(b"Cookie authentication is disabled by the server.", response.data)
        self.assertNotIn(b"ENABLE_BROWSER_COOKIES=1", response.data)
        self.assertNotIn(b"ENABLE_COOKIE_AUTH=1", response.data)
        configured_mib = int(round(downloader.MAX_DOWNLOAD_BYTES / (1024 * 1024)))
        self.assertIn(f"configured {configured_mib} MiB cap".encode(), response.data)

    def test_homepage_has_server_rendered_search_and_social_metadata(self):
        with patch.object(downloader, "PUBLIC_SITE_URL", "https://social.example.test"):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("<title>Social Media Video &amp; Audio Downloader | Lee Tech</title>", html)
        self.assertIn('property="og:title" content="Social Media Video &amp; Audio Downloader | Lee Tech"', html)
        self.assertIn('name="twitter:title" content="Social Media Video &amp; Audio Downloader | Lee Tech"', html)
        self.assertIn('name="description" content="Download public videos, audio, and images from supported social media links.', html)
        self.assertIn('property="og:description" content="Download public videos, audio, and images from supported social media links.', html)
        self.assertIn('name="keywords" content="media downloader, social media downloader, video downloader, audio downloader, Media Downloader"', html)
        self.assertIn('name="robots" content="index,follow"', html)
        self.assertIn('<link rel="canonical" href="https://social.example.test/">', html)
        self.assertIn('property="og:type" content="website"', html)
        self.assertIn('property="og:url" content="https://social.example.test/"', html)
        self.assertIn('property="og:image" content="https://social.example.test/static/social-preview.jpg"', html)
        self.assertIn('property="og:image:width" content="1200"', html)
        self.assertIn('property="og:image:height" content="630"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertIn('name="twitter:image" content="https://social.example.test/static/social-preview.jpg"', html)
        self.assertIn('name="application-name" content="Media Downloader by Lee Tech"', html)
        self.assertIn('"@type": "WebSite"', html)
        self.assertIn('"@id": "https://social.example.test/#website"', html)
        self.assertIn('"alternateName": "Media Downloader"', html)
        self.assertIn('"@type": "WebApplication"', html)
        self.assertIn('"@id": "https://social.example.test/#webapplication"', html)
        self.assertIn('"applicationCategory": "MultimediaApplication"', html)
        self.assertIn('"isAccessibleForFree": true', html)
        self.assertIn('"price": 0', html)
        self.assertNotIn('"aggregateRating"', html)
        self.assertNotIn('"review"', html)
        self.assertIn('<h2 id="platform-title">Supported social media platforms</h2>', html)
        self.assertIn("YouTube, TikTok, Instagram, Facebook, X, Reddit", html)
        self.assertIn('<h1 id="page-title">Social media downloader<br><span>for video and audio.</span></h1>', html)


    def test_docs_page_is_server_rendered_indexable_and_has_route_specific_metadata(self):
        with patch.object(downloader, "PUBLIC_SITE_URL", "https://social.example.test"):
            response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Robots-Tag", response.headers)
        html = response.get_data(as_text=True)
        self.assertIn("<title>Social Media Video &amp; Audio Downloader Guide | Lee Tech</title>", html)
        self.assertIn('name="robots" content="index,follow"', html)
        self.assertIn('<link rel="canonical" href="https://social.example.test/docs">', html)
        self.assertIn('property="og:url" content="https://social.example.test/docs"', html)
        self.assertIn('"@type": "WebPage"', html)
        self.assertIn('name="description" content="Learn how to download public social media videos and audio.', html)
        self.assertIn('property="og:image:alt" content="Social Media Video and Audio Downloader guide by Lee Tech."', html)
        self.assertIn('name="twitter:image:alt" content="Media Downloader documentation for supported social media links."', html)
        self.assertIn('<h2 id="coverage-title">Supported social media and video platforms</h2>', html)
        self.assertIn('<h1 id="page-title">Social media downloader guide</h1>', html)
        self.assertIn("No account or API key required.", html)
        self.assertIn('href="/">Open the downloader', html)
        self.assertNotIn("app.js", html)
        self.assertNotIn("/api/", html)


    def test_robots_and_sitemap_use_the_configured_origin_and_public_route_only(self):
        with patch.object(downloader, "PUBLIC_SITE_URL", "https://social.example.test"):
            robots = self.client.get("/robots.txt")
            sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(robots.status_code, 200)
        self.assertEqual(robots.mimetype, "text/plain")
        rules = robots.get_data(as_text=True)
        self.assertIn("User-agent: *", rules)
        self.assertIn("Allow: /", rules)
        self.assertIn("Disallow: /api/", rules)
        self.assertIn("Disallow: /download", rules)
        self.assertIn("Disallow: /healthz", rules)
        self.assertIn("Sitemap: https://social.example.test/sitemap.xml", rules)
        self.assertEqual(sitemap.status_code, 200)
        self.assertEqual(sitemap.mimetype, "application/xml")
        root = fromstring(sitemap.data)
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locations = [node.text for node in root.findall("s:url/s:loc", namespace)]
        self.assertEqual(locations, ["https://social.example.test/", "https://social.example.test/docs"])

    def test_unconfigured_origin_omits_absolute_metadata_and_sitemap_discovery(self):
        with patch.object(downloader, "PUBLIC_SITE_URL", ""):
            homepage = self.client.get("/").get_data(as_text=True)
            robots = self.client.get("/robots.txt").get_data(as_text=True)
            sitemap = self.client.get("/sitemap.xml")
        self.assertNotIn('rel="canonical"', homepage)
        self.assertNotIn('property="og:image"', homepage)
        self.assertNotIn('name="twitter:image"', homepage)
        self.assertNotIn("Sitemap:", robots)
        self.assertEqual(sitemap.status_code, 503)

    def test_social_preview_image_is_public_and_has_the_declared_mime_type(self):
        response = self.client.get("/static/social-preview.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertNotIn("X-Robots-Tag", response.headers)
        self.assertGreater(len(response.data), 0)
        response.close()

    def test_utility_and_error_responses_are_noindex(self):
        for path in ("/healthz", "/api/platforms", "/manus-routes.json"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")
                response.close()

    def test_manus_route_manifest_is_served_at_root(self):
        response = self.client.get("/manus-routes.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"routes": [
            {"path": "/", "title": "Social Media Video & Audio Downloader | Lee Tech"},
            {"path": "/docs", "title": "Social Media Video & Audio Downloader Guide | Lee Tech"},
        ]})
        response.close()

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_metadata_preview_returns_only_display_fields(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/api/info", data={
            "url": "https://example.com/watch?v=1", "format": "mp4", "quality": "720"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["title"], "Sample media safe title")
        self.assertEqual(response.json["duration"], 123)
        self.assertEqual(response.json["media_type"], "video")
        self.assertTrue(response.json["has_audio"])
        self.assertNotIn("webpage_url", response.json)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", AudioOnlyYoutubeDL)
    def test_preview_detects_audio_without_failing_an_explicit_mp4_choice(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/api/info", data={
            "url": "https://example.com/audio", "format": "mp4", "quality": "720"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["media_type"], "audio")
        self.assertTrue(response.json["has_audio"])

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_preview_rate_limit_is_per_client_and_returns_retry_after(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "PREVIEW_LIMIT_PER_MINUTE", 1), \
                patch.object(downloader, "PREVIEW_LIMIT_PER_HOUR", 10):
            first = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            }, headers={"X-Forwarded-For": "203.0.113.100"})
            blocked = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            }, headers={"X-Forwarded-For": "198.51.100.200"})
            other_client = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            }, environ_overrides={"REMOTE_ADDR": "198.51.100.8"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        self.assertEqual(blocked.json["code"], "rate_limited")
        self.assertIn("Preview limit reached", blocked.json["error"])
        self.assertEqual(other_client.status_code, 200)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_download_rate_limit_applies_before_another_extraction(self, resolver):
        resolver.side_effect = self.public_dns
        data = {"url": "https://example.com/watch", "format": "mp4", "quality": "720"}
        with patch.object(downloader, "DOWNLOAD_LIMIT_PER_HOUR", 1), \
                patch.object(downloader, "DOWNLOAD_LIMIT_PER_DAY", 10):
            first = self.client.post("/download", data=data,
                                     headers={"Accept": "application/octet-stream"})
            first.close()
            blocked = self.client.post("/download", data=data,
                                       headers={"Accept": "text/html"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        self.assertIn(b"Download limit reached", blocked.data)

    def test_rate_limit_bucket_table_is_bounded_and_fails_closed(self):
        with patch.object(downloader, "MAX_RATE_LIMIT_BUCKETS", 1):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(downloader._RATE_LIMIT_BUCKETS, {})
        self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_invalid_download_format_is_rejected_before_extractor_runs(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/watch", "format": "exe", "quality": "best"
        }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "invalid_request")
        self.assertIsNone(FakeYoutubeDL.last_options)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_invalid_explicit_quality_is_rejected_before_extractor_runs(self, resolver):
        resolver.side_effect = self.public_dns
        for format_type, quality in (("mp4", "9999"), ("mp3", "999")):
            with self.subTest(format=format_type, quality=quality):
                response = self.client.post("/download", data={
                    "url": "https://example.com/watch", "format": format_type, "quality": quality
                }, headers={"Accept": "application/json"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["code"], "invalid_request")
                self.assertIsNone(FakeYoutubeDL.last_options)

    def test_oversized_request_returns_structured_413(self):
        with patch.dict(downloader.app.config, {"MAX_CONTENT_LENGTH": 128}):
            response = self.client.post("/api/info", data={"url": "https://example.com", "extra": "x" * 256},
                                        headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json["code"], "request_too_large")
        self.assertIn("too large", response.json["error"])

    def test_busy_download_returns_machine_code_and_retry_after(self):
        with patch.object(downloader, "_acquire_slot", return_value=False):
            response = self.client.post("/download", data={"url": "https://example.com/watch"},
                                        headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json["code"], "busy")
        self.assertEqual(response.headers["Retry-After"], "15")

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FailingYoutubeDL)
    def test_source_rate_limit_is_actionable_and_structured(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/watch", "format": "mp4", "quality": "720"
        }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["code"], "source_error")
        self.assertIn("temporarily limiting", response.json["error"])
        self.assertNotIn("HTTP Error 429", response.json["error"])

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FailingYoutubeDL)
    def test_authentication_failure_on_public_service_does_not_suggest_cookie_upload(self, resolver):
        resolver.side_effect = self.public_dns
        FailingYoutubeDL.failure_message = "Sign in to confirm your age"
        try:
            with patch.object(downloader, "COOKIE_AUTH_ENABLED", False):
                response = self.client.post("/api/info", data={
                    "url": "https://example.com/watch", "format": "mp4", "quality": "720"
                })
        finally:
            FailingYoutubeDL.failure_message = "HTTP Error 429: Too Many Requests"
        self.assertEqual(response.status_code, 502)
        self.assertIn("public service cannot use account cookies", response.json["error"])
        self.assertNotIn("upload a cookie", response.json["error"].lower())

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", LongYoutubeDL)
    def test_duration_limit_returns_clear_invalid_request(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "MAX_DURATION_SECONDS", 60):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "invalid_request")
        self.assertIn("longer than", response.json["error"])

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_oversized_completed_media_is_rejected_before_transfer(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "MAX_DOWNLOAD_BYTES", 5):
            response = self.client.post("/download", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720"
            }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "invalid_request")
        self.assertIn("configured download-size limit", response.json["error"])

    def test_quality_presets_are_bounded(self):
        options = downloader._options("mp4", "720", "%(id)s.%(ext)s")
        self.assertIn("height<=720", options["format"])
        self.assertNotIn("/best", options["format"].split("/", 1)[-1])

    def test_auto_mode_leaves_source_format_unforced(self):
        options = downloader._options("auto", "best", "%(id)s.%(ext)s")
        self.assertEqual(options, {"outtmpl": "%(id)s.%(ext)s"})

    def test_original_mode_keeps_extractor_selected_media_format(self):
        options = downloader._options("original", "best", "%(id)s.%(ext)s")
        self.assertIn("best", options["format"])
        self.assertNotIn("merge_output_format", options)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", AudioOnlyYoutubeDL)
    def test_mp4_is_rejected_for_audio_only_media(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/audio", "format": "mp4", "quality": "720"
        }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("audio-only", response.json["error"])
        self.assertIn("Auto or MP3", response.json["error"])

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", ImageYoutubeDL)
    def test_mp3_is_rejected_for_image_media(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/photo", "format": "mp3", "quality": "192"
        }, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("images", response.json["error"])
        self.assertIn("Auto", response.json["error"])

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_auto_download_uses_extractor_default_selection(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/watch?v=1"
        }, headers={"Accept": "application/octet-stream"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("format", FakeYoutubeDL.last_options)
        self.assertNotIn("merge_output_format", FakeYoutubeDL.last_options)
        response.close()

    def test_platform_examples_come_from_installed_extractors(self):
        response = self.client.get("/api/platforms")
        self.assertEqual(response.status_code, 200)
        self.assertIn("YouTube", response.json["examples"])
        self.assertIn("TikTok", response.json["examples"])
        self.assertIn("many other extractors", response.json["note"])

    def test_homepage_server_renders_platform_examples(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<span class="platform-chip">YouTube</span>', html)
        self.assertIn('<span class="platform-chip">TikTok</span>', html)
        self.assertIn("Examples only. yt-dlp also includes many other extractors", html)
        self.assertNotIn("Loading…", html)

    def test_cookie_upload_is_rejected_while_disabled(self):
        cookie_file = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t2147483647\tname\tvalue\n"
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", False), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookies": (BytesIO(cookie_file), "cookies.txt"),
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", response.json["error"])

    def test_cookie_manager_is_only_rendered_when_enabled(self):
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", False):
            disabled_page = self.client.get("/")
        self.assertNotIn(b'id="cookie-dropzone"', disabled_page.data)
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True):
            enabled_page = self.client.get("/")
        self.assertIn(b'id="cookie-dropzone"', enabled_page.data)
        self.assertIn(b'id="remove-cookie"', enabled_page.data)
        self.assertIn(b'data-max-bytes="2097152"', enabled_page.data)

    def test_browser_profile_controls_are_opt_in_and_warn_about_local_scope(self):
        with patch.object(downloader, "ENABLE_BROWSER_COOKIES", False):
            disabled_page = self.client.get("/")
        self.assertNotIn(b'id="browser-cookie-browser"', disabled_page.data)
        with patch.object(downloader, "ENABLE_BROWSER_COOKIES", True):
            enabled_page = self.client.get("/")
        self.assertIn(b'id="browser-cookie-browser"', enabled_page.data)
        self.assertIn(b'Browser extension-only storage is not scanned', enabled_page.data)
        self.assertIn(b'name="csrf_token"', enabled_page.data)

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_browser_profile_cookies_are_opt_in_consented_and_passed_to_ytdlp(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "ENABLE_BROWSER_COOKIES", True):
            self.client.get("/")
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session["csrf_token"]
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "browser_cookie_browser": "firefox", "browser_cookie_profile": "default-release",
                "browser_cookie_keyring": "kwallet6", "browser_cookie_ack": "1", "csrf_token": csrf_token,
            }, headers={"Origin": "http://localhost"})
            download_response = self.client.post("/download", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "browser_cookie_browser": "firefox", "browser_cookie_profile": "default-release",
                "browser_cookie_keyring": "kwallet6", "browser_cookie_ack": "1", "csrf_token": csrf_token,
            }, headers={"Origin": "http://localhost", "Accept": "application/octet-stream"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeYoutubeDL.browser_options, ("firefox", "default-release", "kwallet6", None))
        self.assertEqual(download_response.status_code, 200, download_response.get_data(as_text=True))
        self.assertEqual(FakeYoutubeDL.browser_options, ("firefox", "default-release", "kwallet6", None))
        download_response.close()

    @patch("app.socket.getaddrinfo")
    def test_browser_profile_access_rejects_missing_consent_bad_csrf_and_paths(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "ENABLE_BROWSER_COOKIES", True):
            self.client.get("/")
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session["csrf_token"]
            base = {"url": "https://example.com/watch", "format": "mp4", "quality": "720",
                    "browser_cookie_browser": "chrome", "csrf_token": csrf_token,
                    "browser_cookie_ack": "1"}
            no_consent = self.client.post("/api/info", data={**base, "browser_cookie_ack": ""},
                                          headers={"Origin": "http://localhost"})
            bad_csrf = self.client.post("/api/info", data={**base, "csrf_token": "wrong"},
                                        headers={"Origin": "http://localhost"})
            unsafe_profile = self.client.post("/api/info", data={**base, "browser_cookie_profile": "../Default"},
                                               headers={"Origin": "http://localhost"})
        self.assertEqual(no_consent.status_code, 400)
        self.assertIn("Confirm", no_consent.json["error"])
        self.assertEqual(bad_csrf.status_code, 400)
        self.assertIn("security check", bad_csrf.json["error"])
        self.assertEqual(unsafe_profile.status_code, 400)
        self.assertIn("profile name only", unsafe_profile.json["error"])

    @patch("app.socket.getaddrinfo")
    def test_browser_profile_access_rejects_remote_or_cross_origin_requests(self, resolver):
        resolver.side_effect = self.public_dns
        with patch.object(downloader, "ENABLE_BROWSER_COOKIES", True):
            self.client.get("/")
            with self.client.session_transaction() as browser_session:
                csrf_token = browser_session["csrf_token"]
            data = {"url": "https://example.com/watch", "format": "mp4", "quality": "720",
                    "browser_cookie_browser": "chrome", "browser_cookie_ack": "1", "csrf_token": csrf_token}
            remote = self.client.post("/api/info", data=data, headers={"Origin": "http://localhost"},
                                      environ_overrides={"REMOTE_ADDR": "203.0.113.10"})
            cross_origin = self.client.post("/api/info", data=data, headers={"Origin": "http://evil.example"})
        self.assertEqual(remote.status_code, 400)
        self.assertIn("only from this machine", remote.json["error"])
        self.assertEqual(cross_origin.status_code, 400)
        self.assertIn("same-origin", cross_origin.json["error"])

    def test_cookie_upload_requires_explicit_acknowledgment(self):
        cookie_file = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t2147483647\tname\tvalue\n"
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookies": (BytesIO(cookie_file), "cookies.txt"),
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("trust this server", response.json["error"])

    def test_cookie_upload_over_remote_http_is_rejected(self):
        cookie_file = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t2147483647\tname\tvalue\n"
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookie_ack": "1", "cookies": (BytesIO(cookie_file), "cookies.txt"),
            }, environ_overrides={"REMOTE_ADDR": "203.0.113.10", "wsgi.url_scheme": "http"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("HTTPS", response.json["error"])

    def test_invalid_cookie_jar_is_rejected(self):
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookie_ack": "1", "cookies": (BytesIO(b"not a cookie jar"), "cookies.txt"),
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Netscape", response.json["error"])

    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_cookie_jar_is_ephemeral_and_permission_restricted_for_preview(self):
        cookie_file = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t2147483647\tname\tvalue\n"
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/api/info", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookie_ack": "1", "cookies": (BytesIO(cookie_file), "cookies.txt"),
            }, environ_overrides={"REMOTE_ADDR": "93.184.216.34", "wsgi.url_scheme": "https"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FakeYoutubeDL.cookie_mode, 0o600)
        self.assertIsNotNone(FakeYoutubeDL.cookie_path)
        self.assertFalse(os.path.exists(FakeYoutubeDL.cookie_path))

    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_cookie_jar_is_removed_after_download_transfer(self):
        cookie_file = b"# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t2147483647\tname\tvalue\n"
        with patch.object(downloader, "COOKIE_AUTH_ENABLED", True), patch("app.socket.getaddrinfo", side_effect=self.public_dns):
            response = self.client.post("/download", data={
                "url": "https://example.com/watch", "format": "mp4", "quality": "720",
                "cookie_ack": "1", "cookies": (BytesIO(cookie_file), "cookies.txt"),
            }, headers={"Accept": "application/octet-stream"})
        self.assertEqual(response.status_code, 200)
        cookie_path = FakeYoutubeDL.cookie_path
        self.assertTrue(os.path.exists(cookie_path))
        self.assertEqual(response.data, b"sample media bytes")
        response.close()
        self.assertFalse(os.path.exists(cookie_path))

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_download_streams_attachment_and_cleans_temp_file(self, resolver):
        resolver.side_effect = self.public_dns
        before = set(os.listdir(tempfile.gettempdir()))
        response = self.client.post("/download", data={
            "url": "https://example.com/watch?v=1", "format": "mp4", "quality": "720"
        }, headers={"Accept": "application/octet-stream"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertEqual(int(response.headers["Content-Length"]), len(b"sample media bytes"))
        self.assertEqual(response.mimetype, "video/mp4")
        self.assertTrue(response.headers["Content-Disposition"].endswith("safe title.mp4\"") or "safe%20title.mp4" in response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"sample media bytes")
        response.close()
        after = set(os.listdir(tempfile.gettempdir()))
        self.assertEqual({item for item in after - before if item.startswith("web-downloader-")}, set())

    @patch("app.socket.getaddrinfo")
    @patch("app.yt_dlp.YoutubeDL", FakeYoutubeDL)
    def test_direct_browser_error_is_html(self, resolver):
        resolver.side_effect = self.public_dns
        response = self.client.post("/download", data={
            "url": "https://example.com/watch?v=1", "format": "exe", "quality": "best"
        }, headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Download not completed", response.data)
        self.assertIn(b'name="robots" content="noindex,nofollow"', response.data)
        self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow")
        self.assertIn(b"invalid_request", response.data)
        self.assertIn(b'by <strong>Lee Tech</strong>', response.data)
        self.assertIn(b"Only save content you have permission to download.", response.data)


if __name__ == "__main__":
    unittest.main()
