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

For phone or LAN testing with the patched public-TLS APK, the default runner
advertises the portable lab asset host:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1
```

That defaults to:

```text
SAOVS_ASSET_BASE=https://assets-os-login-lab.saovs.com/
SAOVS_AUTH_RESULT_ORIGIN=https://assets-os-login-lab.saovs.com
```

Point those hostnames to whichever laptop is currently hosting the server from
the SAOVS DNS/VPN redirector. If you need an IP callback for an older local
test, set it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1 -AuthResultOrigin http://10.202.154.166
```

When using a public Let's Encrypt certificate, put the exported PEM files here:

```text
certs\public-saovs\fullchain.pem
certs\public-saovs\privkey.pem
```

The runner uses those files automatically. You can still pass them explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_private_server.ps1 `
  -CertFile "C:\SAOVS_PS\certs\public-saovs\fullchain.pem" `
  -KeyFile "C:\SAOVS_PS\certs\public-saovs\privkey.pem"
```

The runner accepts either the top-level `content/SAOVS` folder or the inner
Android `files` folder that contains `sword.db`. On this machine the local
fallback points to Adam's existing working content folder:

```text
C:\Users\Adam\SAOVS_Project\SAOVS\data1\com.bandainamcoent.saovsww\files
```

Check health:

```powershell
curl.exe -u admin:admin http://127.0.0.1:8000/admin/health
curl.exe -u admin:admin http://127.0.0.1:8000/admin/users
```

The full-health page also probes the real public game domains, not only the
local Flask process. This matters on VPS because the browser dashboard may be
served by the HTTP process while the game uses the HTTPS API/asset process:

```text
http://127.0.0.1:8000/admin/full-health
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

Set `SAOVS_ADMIN_USERNAME` and `SAOVS_ADMIN_PASSWORD` before exposing the
server. They default to `admin` / `admin` for local setup, so change them in
`.env` on any shared or public host. `SAOVS_ADMIN_TOKEN` is still accepted for
scripted JSON API access.

## Admin Dashboard

The server includes a browser dashboard at `/admin`. It is protected with HTTP
Basic authentication. It shows server health, known users, live parsed
request/reply logs, asset/API/auth categories, and a raw detail pane for each
log entry. The dashboard also has search, filtering, auto-refresh, player
deletion, and a clear-logs action.

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
GET  /admin/full-health
GET  /admin/api/full-health
GET  /admin/users
GET  /admin/api/logs?limit=250
POST /admin/api/logs/clear
```

Log in with `SAOVS_ADMIN_USERNAME` and `SAOVS_ADMIN_PASSWORD`. If
`SAOVS_ADMIN_TOKEN` is set, scripts can still pass it via `X-Admin-Token`.

The PowerShell runner defaults to `SAOVS_SERVER_BACKEND=cheroot`, a production
WSGI server with a fixed thread pool. This is more reliable than Flask's
development server for long-lived HTTPS and multi-gigabyte asset downloads.
After pulling this branch on a VPS, run `python -m pip install -r requirements.txt`
once so `cheroot` is installed.

## Player Equipment Customizer

Players can open the browser customizer at:

```text
https://customizeequipement.saovs.com/
```

The corrected spelling `customizeequipment.saovs.com` is accepted too. Point
either hostname to this same server. The page also works locally at:

```text
http://127.0.0.1:8000/customize
```

Players log in with the same email/password used for transfer. The current
first slice edits ability cards. Owned cards are stored as copy groups, so the
same card can have separate groups with different level, potential, locked
state, and copy counts. Catalog actions add new copies, while owned actions
edit or delete a selected copy group. Changes are stored in SQLite and the
server rebuilds the per-player `ability/index` payload from that database
state.

The game also supplies three maxed default copies of each catalog ability from
`user_list.db`. The customizer stores those defaults as non-editable groups and
excludes them from generated transfer payloads, so customizer edits are added
on top without duplicating the game-provided cards.

The ability catalog lives at `content/customizer/ability_catalog.json`. Card
art is cached on demand into `content/customizer/ability_images/`; that image
cache is ignored by Git so it can grow locally or on the VPS without bloating
the repository.

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
$env:SAOVS_AUTH_RESULT_ORIGIN = "https://assets.example.com"
$env:SAOVS_RELATIVE_AUTH_RESULT_ORIGIN = "https://assets.example.com"
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
- Split static assets to object storage/CDN once paths are stable.
- Record and implement each missing gameplay route from `runtime/logs`.

## Local Verification

This project was run on ports `80` and `443` against BlueStacks with the existing
lab hosts entries. The client reached the home screen. Screenshot:

```text
runtime/logs/saovs_private_server_home_20260509_200438.png
```
