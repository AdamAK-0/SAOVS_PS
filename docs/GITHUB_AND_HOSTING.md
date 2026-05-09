# GitHub And Hosting Plan

Target repository:

```text
https://github.com/AdamAK-0/SAOVS_PS.git
```

## What Goes In GitHub

Commit:

- `src/`
- `scripts/`
- `docs/`
- `config/*.example`
- `content/README.md`
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `.gitignore`
- `.dockerignore`

Do not commit:

- `content/files/`
- `runtime/`
- `certs/`
- local `.env` files
- logs, SQLite runtime DBs, Python caches

The current `content/files` path is a junction to the saved Android data folder.
Keep it local or upload it to the host separately.

## Clean Local Repo Setup

This folder is currently inside a broader Git worktree on Adam's machine, so
create a fresh clone/folder before pushing:

```powershell
mkdir C:\Users\Adam\SAOVS_PS
robocopy C:\Users\Adam\SAOVS_PrivateServer C:\Users\Adam\SAOVS_PS /E /XD runtime certs "content\files" __pycache__ /XF *.pyc *.log *.sqlite3 *.db-shm *.db-wal
cd C:\Users\Adam\SAOVS_PS
git init
git branch -M main
git remote add origin https://github.com/AdamAK-0/SAOVS_PS.git
git add .
git commit -m "Initial SAOVS private server"
git push -u origin main
```

If GitHub says the remote already exists and has files, clone it first, then
copy these project files into the clone.

## Hosting Shape

Use two public hostnames when you are ready for a real public test:

```text
api.example.com     -> Python API server
assets.example.com  -> content files, CDN, or same Python server asset routes
```

For the first working deployment, use one VPS with Docker:

```powershell
git clone https://github.com/AdamAK-0/SAOVS_PS.git
cd SAOVS_PS
mkdir -p runtime content/files
# upload/copy the 3.7 GB content into content/files
docker compose up -d --build
```

Put Caddy or nginx in front for real HTTPS and point both API and asset
hostnames to the app on `127.0.0.1:8000`.

## Disk Strategy

For two players, simplest is:

- VPS disk: 40 GB or larger.
- `content/files`: the 3.7 GB saved Android data content.
- `runtime`: SQLite DB and logs.
- Nightly backup of `runtime/saovs.sqlite3`.
- Occasional backup/snapshot of `content/files`.

For more players or faster downloads:

- Keep API on the VPS.
- Move `content/files` to object storage/CDN.
- Set `SAOVS_ASSET_BASE=https://assets.example.com/`.
- Keep relative paths identical to the current content root.

## Client/Routing Reminder

Hosting only makes the server reachable. It does not by itself solve the client
routing/certificate layer. In the lab, DNS/VPN/Frida handle that separately.
For any non-lab client, use only a client configuration/build you are allowed to
modify and a hostname/certificate setup that client can trust.

