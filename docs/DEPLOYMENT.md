# Deployment Notes

Recommended public shape:

```text
mobile client
  -> https://api.example.com
  -> https://assets.example.com
  -> reverse proxy / CDN
  -> SAOVS private server app
```

Run the Python app on an internal port:

```powershell
$env:SAOVS_ASSET_BASE = "https://assets.example.com/"
$env:SAOVS_ASSET_HOSTS = "assets.example.com"
$env:SAOVS_ADMIN_TOKEN = "<long-random-secret>"
python -m saovs_private_server.compat_server --host 127.0.0.1 --port 8000
```

Example Caddyfile:

```text
api.example.com {
    reverse_proxy 127.0.0.1:8000
}

assets.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Example nginx sketch:

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com assets.example.com;

    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

For assets at scale, move `content/files` to object storage or a CDN once the
paths are stable. The app can stay as the compatibility/API layer.

## Large Asset Content

Do not commit the saved `Android/data` content or UnityCache data to GitHub.
Keep GitHub for source code, docs, and config examples.

For the roughly 3.7 GB content folder, use one of these instead:

- A server disk path mounted as `SAOVS_CONTENT_ROOT`.
- Object storage/CDN with the same relative paths.
- A temporary release/archive only for moving the files, not as the normal live
  asset host.

The client downloads assets as it needs them. It first asks the API for version
and asset host data, then requests catalogs, DB files, and Unity bundle data
from `SAOVS_ASSET_BASE`. Watch `runtime/logs/saovs_private_server.log` for
`[ASSET] serving ...` and `[ASSET] missing ...` lines.
