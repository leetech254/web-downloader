# Media Downloader

A small Flask interface around [yt-dlp](https://github.com/yt-dlp/yt-dlp) for previewing and downloading a single media item as MP4 video, MP3 audio, or its extracted format. It supports the sites and media formats currently handled by yt-dlp; availability can change when source sites change.

## Features

- Link validation and metadata preview before downloading.
- MP4 video presets through 4K, best-available video, and MP3 output at 128–320 kbps.
- An auto/original mode for photos and media types that should retain their extracted file format.
- Automatic platform detection using yt-dlp's extractor registry, with common platform examples taken from the installed version rather than a fixed supported-site allow-list.
- Optional, disabled-by-default Netscape cookie-file authentication for media you can access with your own account.
- A browser-only cookie-file picker with drag/drop, replace/remove, size feedback, and no localStorage or IndexedDB persistence.
- Optional, disabled-by-default yt-dlp cookie reading from a supported browser profile on the same machine as the app.
- Anonymous, per-client preview and download rate limits with bounded in-memory counters.
- Native browser attachment downloads, so large files are not buffered in page JavaScript.
- Playlist rejection, configurable duration and file-size caps, bounded concurrent work, request timeouts, and temporary-file cleanup.
- No built-in login, analytics, admin PIN, or third-party database integration.

## Run locally

Python 3.10+ and FFmpeg are required. Install the system FFmpeg package, then:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`. The test suite uses mocked extractor responses and does not download real media:

```sh
python -m unittest -v
```

To expose the optional cookie-file field for a local test, start with `ENABLE_COOKIE_AUTH=1 python app.py`. Upload a Netscape-format cookie export for the target site and check the consent box. Remote uploads require HTTPS; the default local server accepts cookie uploads only from localhost.

To enable browser-profile cookie access during a local run, use `ENABLE_BROWSER_COOKIES=1 python app.py` and open the app through `http://localhost:5000`. Select a supported browser and explicitly authorize access for each request. The browser profile must be on the computer running Flask; this does not read the visitor's browser profile on a remote computer.

## Configuration

All settings are optional environment variables:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `PORT` | `5000` | Listening port for the development server/container. |
| `SOCKET_TIMEOUT` | `30` | yt-dlp socket timeout in seconds (minimum 5). |
| `MAX_DOWNLOAD_BYTES` | `1073741824` | Maximum size accepted (1 GiB by default). |
| `MAX_DURATION_SECONDS` | `14400` | Maximum media duration (4 hours by default). |
| `MAX_CONCURRENT_JOBS` | `2` | Concurrent extractor jobs per application process. |
| `PREVIEW_LIMIT_PER_MINUTE` / `PREVIEW_LIMIT_PER_HOUR` | `10` / `30` | Per-client metadata preview caps. |
| `DOWNLOAD_LIMIT_PER_HOUR` / `DOWNLOAD_LIMIT_PER_DAY` | `3` / `10` | Per-client download caps. |
| `MAX_RATE_LIMIT_BUCKETS` | `10000` | Maximum in-memory per-client window buckets; when full, new identities are temporarily rejected rather than growing memory without bound. |
| `ENABLE_COOKIE_AUTH` | `0` | Set to `1` to permit per-request cookie uploads. Keep disabled on public/shared instances. |
| `ENABLE_BROWSER_COOKIES` | `0` | Set to `1` to show optional local browser-profile access. Requests are still restricted to localhost, same-origin CSRF-protected requests and are rejected behind trusted proxy headers. |
| `MAX_COOKIE_BYTES` | `2097152` | Maximum uploaded cookie file size (2 MiB by default). |
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

## Security and privacy

Cookie authentication is disabled by default. When explicitly enabled, the app accepts one Netscape-format cookie file for that request, validates its structure, writes it with restrictive permissions inside a private temporary directory, and removes it with the request/download temporary files. In the browser, the selected file stays in the current page session only; its contents are not read or shown, and the page does not save it to localStorage or IndexedDB. The file is sent only when the user submits Preview or Download and acknowledges the upload. Cookie content is not logged or returned. Remote cookie uploads are rejected unless Flask sees HTTPS; localhost development is allowed. Behind a TLS-terminating reverse proxy, set `TRUST_PROXY_HEADERS=1` only if the proxy is trusted, overwrites forwarded headers, and direct app access is blocked. Do not enable this on a public/shared service. Upload a cookie export limited to the target site. Cookies are bearer credentials that can grant account access; use a separate browser profile and revoke the session if it was uploaded to an untrusted host.

Browser-profile access is also disabled by default. When enabled, the backend asks yt-dlp to read cookies from one of its supported browser profiles on the machine running Flask. It is accepted only from a loopback client using a localhost host and same-origin request with a session CSRF token, and is disabled when `TRUST_PROXY_HEADERS=1`. Each request requires explicit consent. The profile value is a name, not a filesystem path, and browser-profile access cannot be combined with an uploaded cookie file in one request. This mode is intended for a local app with your own account; never expose it as a public/shared service. Close the browser if its cookie database is locked. Browser extension local storage or extension credentials are not scraped; only the browser cookie store supported by yt-dlp is used. Actual support depends on the operating system, installed browser, profile, and keyring configuration.

Do not add authentication cookies or tokens to this repository. The repository previously tracked a `cookies.txt` file; treat any real session cookies in that file as compromised: revoke those sessions, remove the file from the working tree and repository history, and rotate any other exposed credentials. `.gitignore` and `.dockerignore` now exclude cookie files, but ignore rules do not erase existing Git history.

The downloader passes links to the current yt-dlp extractor registry; it does not hard-code an allow-list of domains. This covers many social, video, and audio services, but it cannot promise every platform or URL will work. With cookie auth enabled, yt-dlp can use a valid session for media the account is authorized and eligible to view. This does not bypass DRM or platform access controls, and may still fail for private posts, expired sessions, unsupported formats, regional restrictions, or when a platform changes its API. Keep yt-dlp updated and use the on-page platform examples as guidance, not a guarantee.

Use downloads only where you have the rights and permission to do so, and follow applicable laws and the source site's terms of service. The project is provided as-is; deployments must also comply with the source platforms' rules.
