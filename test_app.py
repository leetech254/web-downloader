import os
import stat
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

import app as downloader


class FakeYoutubeDL:
    cookie_path = None
    cookie_mode = None
    browser_options = None

    def __init__(self, options):
        self.options = options
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
            "_type": "video",
        }

    def process_ie_result(self, info, download=False):
        target = self.options["outtmpl"].replace("%(ext)s", "mp4")
        with open(target, "wb") as media:
            media.write(b"sample media bytes")
        return info


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        downloader.app.config.update(TESTING=True)
        self.client = downloader.app.test_client()
        FakeYoutubeDL.cookie_path = None
        FakeYoutubeDL.cookie_mode = None
        FakeYoutubeDL.browser_options = None

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
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

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
        self.assertNotIn("webpage_url", response.json)

    def test_invalid_format_is_rejected_before_extractor_runs(self):
        response = self.client.post("/api/info", data={
            "url": "https://localhost/watch", "format": "exe", "quality": "best"
        })
        self.assertEqual(response.status_code, 400)

    def test_quality_presets_are_bounded(self):
        options = downloader._options("mp4", "720", "%(id)s.%(ext)s")
        self.assertIn("height<=720", options["format"])
        self.assertNotIn("/best", options["format"].split("/", 1)[-1])

    def test_original_mode_keeps_extractor_selected_media_format(self):
        options = downloader._options("original", "best", "%(id)s.%(ext)s")
        self.assertIn("best", options["format"])
        self.assertNotIn("merge_output_format", options)

    def test_platform_examples_come_from_installed_extractors(self):
        response = self.client.get("/api/platforms")
        self.assertEqual(response.status_code, 200)
        self.assertIn("YouTube", response.json["examples"])
        self.assertIn("TikTok", response.json["examples"])
        self.assertIn("many other extractors", response.json["note"])

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


if __name__ == "__main__":
    unittest.main()
