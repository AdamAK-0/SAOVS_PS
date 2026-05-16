# SAOVS Private Server

This is the standalone server project extracted from the working SAOVS local
research lab. It is designed to grow into a real account/API/asset server while
keeping the emulator-specific hooks and one-off debug scripts out of the main
server tree.

Current scope:

- Transfer/login compatibility endpoints.
- SQLite users and sessions.
- SAOVS encrypted MessagePack frame handling.
- Basic bootstrap API responses that are enough to reach the home screen in the
  lab client.
- Asset serving for saved DB files, addressable catalog files, and UnityCache
  bundle `__data` files.

Out of scope:

- Packaging or distributing a modified commercial APK.
- Bypassing certificate pinning or app protections for public users.
- Complete gameplay persistence for every menu and quest. Those endpoints need
  to be added as the client requests them.

## Quick Start

From PowerShell:

```powershell
cd C:\Users\Adam\SAOVS_PrivateServer
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1 -HttpOnly
```

For phone or LAN testing, make the BNID callback return to the server by IP.
The runner auto-detects this laptop's LAN IP, or you can set it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1 -AuthResultOrigin http://10.202.154.166
```

When using a public Let's Encrypt certificate, pass the exported PEM files:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1 `
  -CertFile "C:\SAOVS_PS\certs\public-saovs\fullchain.pem" `
  -KeyFile "C:\SAOVS_PS\certs\public-saovs\privkey.pem" `
  -AuthResultOrigin http://10.202.154.166
```

The local dev runner uses `content/files` by default. On this machine that path
is a junction to Adam's existing working content folder:

```text
C:\Users\Adam\SAOVS_Project\SAOVS\data1\com.bandainamcoent.saovsww\files
```

Check health:

```powershell
curl http://127.0.0.1:8000/admin/health
curl http://127.0.0.1:8000/admin/users
```

Open the admin dashboard:

```text
http://127.0.0.1:8000/admin
```

Stop:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_private_server.ps1
```

## Important Paths

- `src/saovs_private_server/compat_server.py` - current server.
- `runtime/saovs.sqlite3` - users and sessions, created on first run.
- `runtime/logs/saovs_private_server.log` - decoded requests and route decisions.
- `content/files` - optional local content folder.
- `config/local.env.example` - environment variables for local setup.
- `docs/CLIENT_OPTIONS.md` - practical client-side options for non-root users.
- `docs/DEPLOYMENT.md` - reverse proxy and public TLS notes.
  It also explains why the 3.7 GB content folder should stay outside GitHub.

Set `SAOVS_ADMIN_TOKEN` before exposing the server. When that variable is set,
admin JSON routes require the token in `X-Admin-Token` or `?token=...`. The
dashboard page still loads so you can paste the token into the UI.

## Admin Dashboard

The server includes a browser dashboard at `/admin`. It shows server health,
known users, live parsed request/reply logs, asset/API/auth categories, and a
raw detail pane for each log entry. The dashboard also has search, filtering,
auto-refresh, and a clear-logs action.

The default art is original SAO-inspired dashboard artwork. For a private local
setup, you can drop your own owned/downloaded images into:

```text
src/saovs_private_server/admin_static/media/
```

Supported names are `hero.jpg`, `banner.jpg`, and `card.jpg`. Those image files
are ignored by Git so they do not get pushed accidentally.

Useful admin routes:

```text
GET  /admin
GET  /admin/health
GET  /admin/users
GET  /admin/api/logs?limit=250
POST /admin/api/logs/clear
```

If `SAOVS_ADMIN_TOKEN` is set, paste the same token into the dashboard token
field. The UI stores it in browser local storage and sends it as
`X-Admin-Token`.

## Public Deployment Shape

For real mobile devices, the server should be hosted behind real HTTPS domains:

```text
api.example.com     -> Python app / API routes
assets.example.com  -> Python app / asset routes, or a CDN using the same paths
```

Set:

```powershell
$env:SAOVS_ASSET_BASE = "https://assets.example.com/"
$env:SAOVS_ASSET_HOSTS = "assets.example.com"
$env:SAOVS_AUTH_RESULT_ORIGIN = "https://api.example.com"
$env:SAOVS_RELATIVE_AUTH_RESULT_ORIGIN = "https://api.example.com"
```

The app can run behind Caddy/nginx on an internal port such as `127.0.0.1:8000`.
Use normal public certificates from Let's Encrypt or your cloud provider.
Local lab certificates are intentionally not committed to GitHub.

## Development Notes

The current API layer intentionally logs unknown routes and returns a successful
empty SAOVS frame. That keeps the client moving while you identify which payloads
need real persistence.

The next practical server milestones are:

- Replace hard-coded bootstrap inventory/party responses with SQLite-backed
  records.
- Add account recovery/transfer code management.
- Add admin authentication before exposing `/admin/*`.
- Split static assets to object storage/CDN once paths are stable.
- Record and implement each missing gameplay route from `runtime/logs`.

## Local Verification

This project was run on ports `80` and `443` against BlueStacks with the existing
lab hosts entries. The client reached the home screen. Screenshot:

```text
runtime/logs/saovs_private_server_home_20260509_200438.png
```
