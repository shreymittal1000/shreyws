# Public website ingress

ShreyWS publishes Launchpad applications through a Cloudflare Tunnel. The
tunnel is outbound-only: no router port-forwarding is required, the host
firewall continues rejecting public traffic to ports 80 and 443, and
application containers never publish host ports.

```text
visitor HTTPS
  -> Cloudflare edge
  -> authenticated outbound tunnel
  -> cloudflared on cloudflare_ingress
  -> Traefik :8088 (Docker network only)
  -> app's dedicated Docker network
  -> container port
```

Traefik's `cloudflare` entrypoint is deliberately not published on the host.
Only `cloudflared` joins its fixed `172.25.0.0/24` Docker network. Launchpad
creates an exact `Host()` router for each approved public application. Unknown
hostnames receive Traefik's default 404 response.

## Activate after buying a domain

1. Add the domain to Cloudflare and complete its nameserver setup.
2. In Cloudflare Zero Trust, create a remotely managed tunnel and copy its
   connector token.
3. Create the tunnel secret on ShreyWS (never commit it):

   ```bash
   install -d -m 700 /srv/shreyws/secrets/cloudflared
   printf 'TUNNEL_TOKEN=%s\n' 'paste-token-here' > /srv/shreyws/secrets/cloudflared/tunnel.env
   chmod 600 /srv/shreyws/secrets/cloudflared/tunnel.env
   ```

4. Create a public hostname in the tunnel dashboard. Point it to the internal
   service `http://traefik:8088`. A wildcard such as `*.apps.example.com` is
   convenient for many Launchpad sites; individual hostnames and additional
   domains can point to the same service too.
5. Enable only the domain suffixes Launchpad may publish:

   ```bash
   install -d -m 700 /srv/shreyws/secrets/launchpad
   cat > /srv/shreyws/secrets/launchpad/public-ingress.env <<'EOF'
   LAUNCHPAD_PUBLIC_DOMAIN_SUFFIXES=apps.example.com
   EOF
   chmod 600 /srv/shreyws/secrets/launchpad/public-ingress.env
   ```

   Multiple suffixes are comma-separated. Prefer a dedicated suffix such as
   `apps.example.com` instead of allowing the entire domain.

6. Start the connector and reload Launchpad:

   ```bash
   cd /srv/shreyws/infra/compose/cloudflared
   docker compose up -d
   cd /srv/shreyws/infra/compose/launchpad
   docker compose up -d
   ```

Launchpad automatically selects **Public · Cloudflare Tunnel** when a domain
is entered, but accepts it only when that domain belongs to an allowlisted
suffix. Leaving the domain blank keeps the existing internal Tailscale route.
Cloudflare provides the public
TLS certificate; the private tunnel-to-Traefik hop uses its isolated Docker
network. Internal applications remain behind Tailscale and Authentik.

## Before publishing an application

- Treat a public container as internet-facing code even though its port is not
  directly exposed.
- Do not publish databases or administrative dashboards.
- Keep CPU, memory, PID, and storage limits enabled.
- Avoid placing secrets in client-side/static website builds.
- Verify the application handles proxy headers safely and has its own
  authentication if private user data is involved.
- Add Cloudflare rate limiting or access policies where appropriate.
