# Media Downloader

A small Flask interface around [yt-dlp](https://github.com/yt-dlp/yt-dlp) for previewing and downloading a single media item. Auto is the default and lets yt-dlp choose a source-compatible format; visitors can explicitly request MP4 video or MP3 audio when compatible. It supports the sites and media formats currently handled by yt-dlp; availability can change when source sites change.

## Features

- Link validation and metadata preview before downloading; pasting a complete HTTP(S) link starts the preview automatically, and the Preview link button remains available.
- Automatic preview detection for video, audio-only, image/photo, or unknown media, with incompatible explicit formats disabled when the source type is known.
- Source-compatible Auto output by default, plus MP4 video presets through 4K and MP3 output at 128–320 kbps when compatible.
- Explicit format and quality changes reuse the current metadata preview; only changes to the link or cookie/profile request state require another preview.
- Automatic platform detection using yt-dlp's extractor registry, with common platform examples taken from the installed version rather than a fixed supported-site allow-list.
- Optional, disabled-by-default Netscape cookie-file authentication for media you can access with your own account.
- A browser-only cookie-file picker with drag/drop, replace/remove, size feedback, and no localStorage or IndexedDB persistence.
- Optional, disabled-by-default yt-dlp cookie reading from a supported browser profile on the same machine as the app.
- Anonymous, per-client preview and download rate limits with bounded in-memory counters.
- End-to-end download feedback: the page shows preparation, received bytes, and a completion handoff, while structured errors provide retry or correction guidance.
- Attachment responses are read by the page to report byte progress, then handed to the browser's Downloads flow. The page temporarily buffers the complete file in browser memory (up to the configured cap, 250 MiB in the public Docker deployment), so large files can use substantial memory.
- Playlist rejection, configurable duration and file-size caps, bounded concurrent work, request timeouts, and temporary-file cleanup.
- No built-in login, analytics, admin PIN, or third-party database integration.

## Run locally

Python 3.10+ and FFmpeg are required to run the app. Node.js 18+ is required only for the browser-side test harness. Install the system FFmpeg package, then:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`. The Python suite uses mocked extractor responses and does not download real media; the frontend suite runs the page script against a small fake DOM and mocked Fetch/streams:

```sh
python -m unittest -v
node --test test_frontend.cjs
```

The download status reports progress only after the server begins returning the file; source extraction and conversion are shown as a preparation state because yt-dlp does not expose a reliable end-user progress stream through this interface. “Transfer finished” means the complete response has reached the page and was handed to the browser. The app cannot confirm that the browser wrote it to disk; check the browser's Downloads list. A browser-side buffer is needed here to provide byte-level progress for the existing POST-with-options flow. Consider available device memory before downloading a file near the configured cap.

AJAX download requests that send `Accept: application/json` receive stable error codes such as `invalid_request`, `busy`, `rate_limited`, `request_too_large`, and `source_error`; busy/rate-limit responses include `Retry-After`. Regular form submissions continue to receive the standalone HTML error page.

To expose the optional cookie-file field for a local test, start with `ENABLE_COOKIE_AUTH=1 python app.py`. Upload a Netscape-format cookie export for the target site and check the consent box. Remote uploads require HTTPS; the default local server accepts cookie uploads only from localhost.

To enable browser-profile cookie access during a local run, use `ENABLE_BROWSER_COOKIES=1 python app.py` and open the app through `http://localhost:5000`. Select a supported browser and explicitly authorize access for each request. The browser profile must be on the computer running Flask; this does not read the visitor's browser profile on a remote computer.

## Configuration

All settings are optional environment variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `PORT` | `5000` | Listening port for the development server/container. |
| `PUBLIC_SITE_URL` | Manus image: `https://mediagrab-kbljqk2v.manus.space`; Vercel image: empty | Explicit public site origin for canonical, sitemap, and social-image URLs. Do not include a path; on Vercel, set this to the production domain. |
| `SOCKET_TIMEOUT` | `30` | yt-dlp socket timeout in seconds (minimum 5). |
| `MAX_DOWNLOAD_BYTES` | `1073741824` | Maximum size accepted (1 GiB by default). |
| `MAX_DURATION_SECONDS` | `14400` | Maximum media duration (4 hours by default). |
| `MAX_CONCURRENT_JOBS` | `2` | Concurrent extractor jobs per application process. |
| `PREVIEW_LIMIT_PER_MINUTE` / `PREVIEW_LIMIT_PER_HOUR` | `10` / `30` | Per-client metadata preview caps. |
| `DOWNLOAD_LIMIT_PER_HOUR` / `DOWNLOAD_LIMIT_PER_DAY` | `3` / `10` | Per-client download caps. |
| `MAX_RATE_LIMIT_BUCKETS` | `10000` | Maximum in-memory per-client window buckets; when full, new identities are temporarily rejected rather than growing memory without bound. |
| `ENABLE_COOKIE_AUTH` | `0` | Set to `1` to permit per-request cookie uploads. Keep disabled on public/shared instances. |
| `ENABLE_BROWSER_COOKIES` | `0` | Set to `1` to show optional local browser-profile access. Requests are still restricted to localhost, same-origin CSRF-protected requests and are rejected behind trusted proxy headers. |
| `SESSION_COOKIE_SECURE` | `0` in direct Python runs; `1` in containers | Mark Flask session cookies Secure for HTTPS deployments. Keep `0` for plain-HTTP local development. Browser-originated preview and download POSTs also validate same-origin and CSRF signals when a session is present. |
| `MAX_COOKIE_BYTES` | `2097152` | Maximum uploaded cookie file size (2 MiB by default). |
| `STATIC_ASSET_VERSION` | Content fingerprint | Optional override for the cache-busting version on CSS, JavaScript, favicon, icon, and manifest URLs. Leave unset to derive it automatically from static asset contents. |
| `TRUST_PROXY_HEADERS` | `0` in direct Python runs | Set to `1` only behind one trusted proxy that overwrites forwarded headers and blocks direct app access. The public Docker image sets this for the Manus ingress and trusts one proxy hop. |
| `FLASK_SECRET_KEY` | Random per process | Set a long, random, stable value when running multiple workers so signed Flask sessions remain valid across workers. Never commit it. |

## Docker

```sh
docker build -t media-downloader .
docker run --rm -p 5000:3000 media-downloader
```

The Docker image uses conservative public-service defaults: one extraction at a time, 250 MiB maximum output, one-hour maximum media duration, and cookie features disabled. Its in-memory per-client limits are 10 previews/minute and 30/hour, plus 3 downloads/hour and 10/day. The Flask development-server defaults shown in the configuration table are less restrictive; use the Docker image for the public deployment.

Rate-limit counters are bounded and process-local: they reset when the instance restarts and are not shared across replicas. This is a temporary, best-effort abuse control, not a guarantee against distributed abuse. No login or Redis is required. The container trusts one forwarded proxy hop for client address/protocol/host; do not copy that setting to a direct deployment unless its proxy overwrites those headers and blocks direct access. Access logs are disabled to avoid routinely recording submitted media URLs and visitor addresses.

For an internet-facing deployment, terminate TLS at a trusted ingress, monitor disk usage, and restrict outbound network access. The URL validator rejects local/private hosts and checks DNS results, but generic media extractors follow redirects and may perform additional requests; a network-level egress policy is still necessary for a public service. The built-in concurrency limit is per process, not distributed across replicas.

## Vercel (experimental)

`Dockerfile.vercel` selects Vercel's Container Runtime rather than the Flask/Python Function preset, while preserving the app's FFmpeg, Gunicorn, non-root user, and conservative public-service defaults. It listens on Vercel's default port 80 and honors the platform-provided `PORT`. The Container Runtime is currently documented as beta; confirm current plan and runtime limits before relying on it for a public service.

To try a deployment, import this GitHub repository into Vercel with the repository root as the project root and let Vercel build the container. Keep cookie authentication and browser-profile access disabled. Set `PUBLIC_SITE_URL` to the stable HTTPS production domain (for example, `https://your-production-domain.example`) so canonical, sitemap, and social-preview URLs do not point to a Preview deployment; `/sitemap.xml` returns a configuration error and absolute metadata is omitted until this value is set. Set a stable random `FLASK_SECRET_KEY` in project environment variables if using multiple workers or instances; never commit secrets. After deployment, test preview, video and audio conversion, error handling, and actual downloads from the target platforms.

Vercel function limits still matter for container deployments. Vercel documents a 300-second maximum duration for Hobby functions, and response bodies are limited to 4.5 MB unless streamed. This app permits outputs up to 250 MiB and source media up to one hour, so large or slow downloads and FFmpeg work are not guaranteed to complete within Hobby limits. Confirm that the deployed runtime streams the attachment successfully and verify real end-to-end timing before treating a Vercel deployment as production-ready; use an external worker/storage architecture if the limits are insufficient. See [Vercel Container Runtime](https://vercel.com/docs/functions/runtimes/container), [function duration](https://vercel.com/docs/functions/configuring-functions/duration), and [function limits](https://vercel.com/docs/functions/limitations).

## HTTPS integration for another website

If you want to use this downloader or reuse its guide on your own site, deploy it behind a stable HTTPS origin such as `https://downloader.example.com`. Terminate TLS at a trusted reverse proxy, forward the original `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto` values, and set `TRUST_PROXY_HEADERS=1` only when the proxy overwrites those headers and direct Flask access is blocked. Set `PUBLIC_SITE_URL` to the same HTTPS origin and use `SESSION_COOKIE_SECURE=1` in production. Keep `FLASK_SECRET_KEY` stable across workers.

The browser UI is intentionally same-origin: its preview and download POSTs use a session CSRF token and same-origin request signals. Do not add permissive CORS or disable CSRF to make a different frontend work. For a separate website, either link users to the hosted `/docs` and `/` pages, serve a reverse-proxied copy under the same origin, or make server-side requests from a backend you control. The metadata endpoint can be called server-side, for example:

```sh
curl -fsS -X POST https://downloader.example.com/api/info \
  -H 'Accept: application/json' \
  --data-urlencode 'url=https://source.example/post/123' \
  --data-urlencode 'format=auto' \
  --data-urlencode 'quality=best'
```

When copying the public guide, retain its safety, permissions, privacy, source-platform, and HTTPS notes. Never copy `FLASK_SECRET_KEY`, session cookies, cookie exports, or other deployment values into your site or frontend code. The endpoint remains rate-limited and is not a guaranteed platform-wide API contract.

For interactive same-origin testing, open `/api-playground` on your HTTPS deployment. It tests `POST /api/info`, displays the structured JSON response, includes the session CSRF token automatically, and never downloads a file. For server-side tooling, import [`postman/media-downloader-https.postman_collection.json`](postman/media-downloader-https.postman_collection.json) into Postman and set the `baseUrl` and `mediaUrl` collection variables.

## Search and share previews

The homepage sends its title, description, canonical URL, Open Graph fields, and X large-image card tags in server-rendered HTML. `/robots.txt` guides crawlers away from utility endpoints, while `/sitemap.xml` lists only the public homepage; these files help discovery but do not guarantee indexing. See Google's [sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview) and the [Open Graph protocol](https://ogp.me/). The Manus container has the current public origin as its default. The Vercel container intentionally leaves `PUBLIC_SITE_URL` empty until the production domain is known, and absolute canonical/social/sitemap URLs are omitted rather than inferred from request or Preview hosts.

Static CSS, JavaScript, favicon, icon, and manifest responses use an automatic content fingerprint in their URLs and are served with a one-year immutable cache policy. When a static file changes, the fingerprint changes on the next process start so visitors receive the new asset without sacrificing repeat-visit caching. The homepage's below-the-fold preview image also uses lazy loading, asynchronous decoding, and explicit dimensions to reduce layout movement.

## Security and privacy

Cookie authentication is disabled by default. When explicitly enabled, the app accepts one Netscape-format cookie file for that request, validates its structure, writes it with restrictive permissions inside a private temporary directory, and removes it with the request/download temporary files. In the browser, the selected file stays in the current page session only; its contents are not read or shown, and the page does not save it to localStorage or IndexedDB. The file is sent only when the user submits Preview or Download and acknowledges the upload. Cookie content is not logged or returned. Remote cookie uploads are rejected unless Flask sees HTTPS; localhost development is allowed. Behind a TLS-terminating reverse proxy, set `TRUST_PROXY_HEADERS=1` only if the proxy is trusted, overwrites forwarded headers, and direct app access is blocked. Do not enable this on a public/shared service. Upload a cookie export limited to the target site. Cookies are bearer credentials that can grant account access; use a separate browser profile and revoke the session if it was uploaded to an untrusted host.

Browser-profile access is also disabled by default. When enabled, the backend asks yt-dlp to read cookies from one of its supported browser profiles on the machine running Flask. It is accepted only from a loopback client using a localhost host and same-origin request with a session CSRF token, and is disabled when `TRUST_PROXY_HEADERS=1`. Each request requires explicit consent. The profile value is a name, not a filesystem path, and browser-profile access cannot be combined with an uploaded cookie file in one request. This mode is intended for a local app with your own account; never expose it as a public/shared service. Close the browser if its cookie database is locked. Browser extension local storage or extension credentials are not scraped; only the browser cookie store supported by yt-dlp is used. Actual support depends on the operating system, installed browser, profile, and keyring configuration.

Do not add authentication cookies or tokens to this repository. The repository previously tracked a `cookies.txt` file; treat any real session cookies in that file as compromised: revoke those sessions, remove the file from the working tree and repository history, and rotate any other exposed credentials. `.gitignore` and `.dockerignore` now exclude cookie files, but ignore rules do not erase existing Git history.

The downloader passes links to the current yt-dlp extractor registry; it does not hard-code an allow-list of domains. This covers many social, video, and audio services, but it cannot promise every platform or URL will work. With cookie auth enabled, yt-dlp can use a valid session for media the account is authorized and eligible to view. This does not bypass DRM or platform access controls, and may still fail for private posts, expired sessions, unsupported formats, regional restrictions, or when a platform changes its API. Keep yt-dlp updated and use the on-page platform examples as guidance, not a guarantee.

Use downloads only where you have the rights and permission to do so, and follow applicable laws and the source site's terms of service. The project is provided as-is; deployments must also comply with the source platforms' rules.
