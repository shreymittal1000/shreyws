# Security Audit - 2026-07-08

Scope: ShreyWS server audit as if the host were about to be exposed to the public Internet.

## Executive Summary

Several high-risk public-exposure issues were found. Safe improvements were applied immediately where they did not redesign the stack or require new access-control decisions. The remaining high-impact items should be handled deliberately before any direct public Internet exposure.

## Critical Findings

### 1. Traefik insecure dashboard was published on all interfaces

Status: Fixed.

Evidence before change:

- Docker published Traefik dashboard/API on `0.0.0.0:8081->8080` and `[::]:8081->8080`.
- Traefik was configured with `--api.insecure=true`.
- `http://127.0.0.1:8081/dashboard/` returned `HTTP/1.1 200 OK`.

Risk:

The insecure Traefik API/dashboard can expose routing and service metadata and should not be directly reachable on an Internet-exposed host.

Change applied:

- Set `--api.insecure=false` in `compose/traefik/compose.yaml`.
- Removed the `8081:8080` published port.
- Recreated only the Traefik container.

Verification:

- `docker ps` no longer shows port `8081` for Traefik.
- `ss` no longer shows a listener on `:8081`.
- HTTPS routes on 443 remained available.

### 2. Temporary passwordless sudo rule existed

Status: Fixed.

Evidence before change:

- `/etc/sudoers.d/90-shreyws-codex-temp` contained `shreyws ALL=(ALL) NOPASSWD:ALL`.

Risk:

Any compromise of the `shreyws` user would immediately become root without requiring a password.

Change applied:

- Removed the temporary passwordless sudo rule after all privileged work was complete.

## High Findings

### 3. SSH accepted password authentication and root login policy allowed root via keys

Status: Fixed.

Evidence before change from `sshd -T`:

- `passwordauthentication yes`
- `permitrootlogin without-password`
- `x11forwarding yes`

Risk:

Password authentication increases brute-force risk on public SSH. Root login should not be available directly. X11 forwarding is unnecessary server-side attack surface for this host.

Change applied:

Created `/etc/ssh/sshd_config.d/99-shreyws-hardening.conf`:

```text
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
X11Forwarding no
```

Verification:

- `sshd -t` passed.
- SSH was reloaded, not restarted.
- Fresh key-only login succeeded with `BatchMode=yes`.
- `sshd -T` now reports password auth and root login disabled.

### 4. GNOME Remote Desktop listened on all interfaces on TCP 3389

Status: Fixed.

Evidence before change:

- `ss` showed `gnome-remote-desktop` listening on `*:3389`.
- User service `gnome-remote-desktop.service` was active and enabled.

Risk:

RDP-style remote desktop services should not be globally exposed on a server intended for public Internet exposure unless deliberately protected with network policy and strong authentication.

Change applied:

- Disabled and stopped `gnome-remote-desktop.service` for user `shreyws`.

Verification:

- User service is `disabled` and `inactive`.
- `ss` no longer shows TCP `:3389`.

### 5. Prometheus and cAdvisor are exposed through HTTPS without an additional authentication layer

Status: Not changed in this pass.

Evidence:

- `/prometheus/`, `/docker/`, and `/containers/` are routed through Traefik over HTTPS.
- Grafana redirects to login, but Prometheus and cAdvisor do not present a login barrier.

Risk:

Prometheus and cAdvisor expose host/container/service metadata. cAdvisor in particular has broad read-only host mounts for metrics collection. This is acceptable on a trusted private network, but not suitable for unauthenticated public exposure.

Recommended next step:

Add Traefik middleware authentication for Prometheus and cAdvisor, or restrict those routes to Tailscale/private access only. This was not changed automatically because it changes how monitoring URLs are accessed and requires a credential/access-control decision.

## Medium Findings

### 6. Firewall default INPUT policy is accept

Status: Not changed in this pass.

Evidence:

- nftables INPUT policy is `accept` for IPv4 and IPv6.
- Docker and Tailscale rules exist, but there is no host-level default-deny policy protecting all non-required inbound services.

Risk:

A new daemon binding to a public interface may become reachable without an explicit allow rule.

Recommended next step:

Design and test a default-deny nftables or UFW policy that explicitly allows SSH, HTTP/HTTPS, Tailscale, and required LAN-only services. This should be done carefully because Docker modifies firewall rules and remote lockout is possible.

### 7. Avahi/mDNS is enabled and listening on UDP 5353

Status: Not changed in this pass.

Risk:

mDNS is usually LAN-scoped, but it is unnecessary on many servers and leaks host presence/name on local networks.

Recommended next step:

Disable Avahi if `.local` discovery is not needed.

### 8. Docker group membership grants root-equivalent control

Status: Not changed in this pass.

Evidence:

- User `shreyws` is in group `docker`.

Risk:

Membership in the Docker group effectively permits root-level host access through containers and bind mounts.

Recommended next step:

Keep this only if day-to-day Docker administration as `shreyws` is required. Otherwise remove the user from `docker` and operate Docker through controlled sudo rules.

## Low Findings

### 9. Monitoring images use `latest` tags

Status: Not changed in this pass.

Risk:

Using `latest` makes rebuilds less reproducible and can introduce unexpected behavior during image pulls.

Recommended next step:

Pin image versions for Grafana, Prometheus, cAdvisor, and Node Exporter after choosing update policy.

## Verification Summary

After applied changes:

- Traefik and Prometheus Compose configs rendered successfully.
- Only Traefik and Prometheus were recreated for Compose changes.
- `docker ps` shows Traefik publishing only ports 80 and 443.
- `ss` no longer shows listeners on 8081 or 3389.
- Key-only SSH login succeeded after SSH hardening.
- Existing HTTPS service routes were checked after changes.
