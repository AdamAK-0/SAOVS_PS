# Client Options For Non-Root Users

The server side is only half of the practical problem. Non-root users cannot
edit `/system/etc/hosts`, install a system CA, or force a commercial game to
trust a different certificate chain.

The workable, supportable options are:

- Use a client you are legally allowed to modify and point it at domains you
  control, then serve those domains with normal public TLS certificates.
- Keep the current rooted-emulator setup for research/dev only.
- Use an official permission/licensing path if this is meant for public players.

This project does not package a modified commercial APK. For a legitimate custom
client, configure it to use your own API and asset domains, for example:

```text
https://api.example.com/
https://assets.example.com/
```

Then set the server environment:

```powershell
$env:SAOVS_ASSET_BASE = "https://assets.example.com/"
$env:SAOVS_ASSET_HOSTS = "assets.example.com"
```

Use Caddy, nginx, or a cloud load balancer to terminate public TLS on ports 443
and route traffic to the Python app.

