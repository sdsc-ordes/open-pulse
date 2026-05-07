# SPARQL reverse proxy (Caddy + HTTP Basic Auth)

Caddy 2 in front of Oxigraph. Public reads on `GET /query` and `GET /`; HTTP Basic Auth
required for writes (`POST /update`, `POST /store`, `PUT /store/*`, `DELETE /store/*`).

Oxigraph has no built-in authentication, so the proxy is the only thing standing between
your store and the open internet whenever the port is exposed. **Always run Oxigraph
behind this proxy in any deploy that publishes the port.**

## Files

| File | Tracked? | Purpose |
| --- | --- | --- |
| `Caddyfile` | yes | Caddy config — auth rules + reverse proxy to Oxigraph |
| `docker-compose.yaml` | yes | standalone deploy (use the main stack for the full setup) |
| `users.example` | yes | example user file format |
| `users` | **no** (gitignored) | real user file — bcrypt hashes |
| `.gitignore` | yes | gitignores `users` |

## First-time setup

1. **Generate a bcrypt hash for each user.** Run inside the Caddy container so you
   match the algorithm Caddy uses for verification:

   ```bash
   docker run --rm caddy:2.8-alpine caddy hash-password --plaintext 'choose-a-strong-password'
   ```

   Output looks like `$2a$14$Bvb5...`. Don't share. Don't commit.

2. **Create `users`** by copying `users.example` and replacing the placeholder hashes:

   ```
   admin   $2a$14$<real-hash-here>
   loader  $2a$14$<another-real-hash-here>
   ```

   One user per line, separated by whitespace. Add as many users as you need —
   typically one per service account that uploads data.

3. **Bring up the stack.** From the repo root:

   ```bash
   docker compose -f infra/compose/docker-compose.yml --profile sparql up -d
   ```

   Or standalone (Caddy alone, requires Oxigraph reachable as `oxigraph:7878` on the
   shared network):

   ```bash
   docker compose up -d
   ```

## Smoke tests

Replace `loader` and `your-password` with values from your `users` file.

```bash
# Public read — no auth, expect 200
curl -i 'http://localhost:7878/query?query=ASK%20%7B%7D'

# Unauthenticated write — expect 401
curl -i -X POST -H 'Content-Type: text/turtle' \
     --data '<urn:a> <urn:b> <urn:c> .' \
     'http://localhost:7878/store?default'

# Authenticated write — expect 201 (first POST to a fresh graph) or 204 (subsequent)
curl -i -u 'loader:your-password' \
     -X POST -H 'Content-Type: text/turtle' \
     --data '<urn:a> <urn:b> <urn:c> .' \
     'http://localhost:7878/store?default'

# Verify the triple was inserted
curl -G --data-urlencode 'query=SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5' \
     'http://localhost:7878/query'
```

## Adding / removing users

Edit `users`, then reload Caddy without restarting:

```bash
docker exec sparql-proxy-open-pulse caddy reload --config /etc/caddy/Caddyfile
```

Caddy will pick up the new file on reload. No downtime.

## Rotating a password

1. Generate a new hash with `caddy hash-password`.
2. Replace the line in `users`.
3. Reload Caddy as above.
4. Notify whoever was using the old password.

## Recovery — lost admin credentials

The `users` file is the only source of truth for passwords. If it's lost:

1. Delete or empty the `users` file (no one can write).
2. Generate a new admin hash.
3. Recreate `users` with the new hash.
4. Reload Caddy.

The Oxigraph data volume is **not affected** — proxy state is purely in this directory.

## Switching to OIDC / SSO later

Replace the `basicauth` block with `caddy-security`'s OAuth/OIDC handler. Outline:

```caddyfile
{
    order authenticate before basicauth
    order authorize before reverse_proxy

    security {
        oauth identity provider epfl {
            realm epfl
            driver generic
            client_id     {$OIDC_CLIENT_ID}
            client_secret {$OIDC_CLIENT_SECRET}
            scopes openid email profile
            base_auth_url https://idp.example/oauth2
            metadata_url  https://idp.example/.well-known/openid-configuration
        }
    }
}

:7878 {
    @writes {
        method POST PUT DELETE PATCH
        path /update /update/* /store /store/*
    }

    handle @writes {
        authenticate with epfl
        reverse_proxy oxigraph:7878
    }

    handle {
        reverse_proxy oxigraph:7878
    }
}
```

This needs the `caddy-security` plugin, which means a custom Caddy build (e.g.
`caddy:2.8-alpine` → a Dockerfile that runs `xcaddy build --with
github.com/greenpau/caddy-security`). That's out of scope for the current migration
plan; track it as a follow-up.

## Limitations

- **No multi-tenancy.** Every authenticated user has the same write access to every
  graph. Per-graph or per-user data isolation is not supported by this setup; it would
  require either a different SPARQL store (e.g. Jena Fuseki with per-dataset
  permissions) or application-layer enforcement.
- **HTTP only by default.** For production, terminate TLS here. Caddy's automatic HTTPS
  only kicks in when you give the site block a real hostname (e.g.
  `sparql.example.org { ... }`) — `:7878` listens on plain HTTP.
- **Single-writer at the storage layer.** Oxigraph (RocksDB) serializes writes
  internally. The proxy doesn't add concurrency limits — add a `rate_limit` directive
  if you need them.
