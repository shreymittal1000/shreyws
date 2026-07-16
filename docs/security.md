# ShreyWS Security Audit

Last updated: 2026-07-16

## Threat Model

ShreyWS is a single-owner homelab server reached primarily through Tailscale and a trusted home LAN. It is not intended to host untrusted users or arbitrary third-party workloads. The main practical risks are remote exposure through LAN/global IPv6 listeners, compromised web applications, leaked runtime secrets, Docker socket abuse, stale packages, and data loss from operational mistakes.

The current hardening target is conservative: reduce avoidable privilege and information exposure without changing Tailscale, Authentik, Traefik routing, storage layout, or remote-access behavior.

## Exposure Model

| Service | Host port | Bind address | Access path | Authentication layer | Intended audience | Risk |
| --- | ---: | --- | --- | --- | --- | --- |
| SSH | 22/tcp | `0.0.0.0`, `::` | Direct host listener | OpenSSH keys only; root login disabled | Owner/admin | Medium: reachable on LAN and global IPv6 if upstream allows it. Password auth is disabled, but scans were observed. |
| xrdp | 3389/tcp | `*` | Direct host listener | RDP login | Owner/admin | Medium/High: useful emergency access, but broad bind address should be restricted by firewall when physical fallback is available. |
| GNOME Remote Desktop | 3390/tcp | `*` user service | Direct host listener | GNOME remote desktop auth | Owner/admin | Medium: user-scoped service listens broadly while active. |
| Traefik HTTP | 80/tcp | `0.0.0.0`, `::` | Docker published port | Redirect/routing layer | Browser clients | Medium: required for HTTP-to-HTTPS routing if LAN clients use it. |
| Traefik HTTPS | 443/tcp | `0.0.0.0`, `::` | Docker published port | Authentik forward-auth on protected services | Browser clients | Medium: protected app entrypoint. Exposure depends on LAN/router/global IPv6 reachability. |
| Tailscale | 41641/udp | `0.0.0.0`, `::` | Tailscale coordination/data path | Tailscale ACL/auth | Owner devices | Low: expected for Tailscale. |
| Tailscale SSH/control | dynamic tailnet TCP | `100.108.162.19`, tailnet IPv6 | Tailscale | Tailscale SSH | Owner/admin | Low: intended private access path. |
| CUPS | 631/tcp | loopback only | Localhost | Local CUPS | Local desktop | Low. |
| Avahi/mDNS | 5353/udp | all interfaces | LAN multicast | None | LAN discovery | Low/Medium: not internet-routable, but unnecessary exposure if mDNS is not used. |
| Prometheus, Grafana, Alertmanager, Loki, Alloy | none published | Docker internal networks | Traefik or internal scrape only | Authentik for browser routes; internal Docker for metrics/logs | Owner/monitoring | Low externally; medium lateral movement risk inside Docker networks. |
| Docker API | Unix socket only | `/var/run/docker.sock` | Host socket bind mounts | Host filesystem permissions | Docker consumers | High if a socket consumer is compromised. |

Local inspection cannot prove whether the home router forwards IPv4 ports or filters global IPv6. Treat services bound to `::` as potentially reachable from outside until router/firewall policy is verified.

## Findings

### High

1. Host firewall input policy is permissive while SSH, RDP, Traefik and Avahi listen on broad addresses.
   - Evidence: nftables `INPUT` policy accepts host traffic; listeners bind to `0.0.0.0`/`::`.
   - Impact: if the LAN or global IPv6 path is reachable, these services are exposed beyond Tailscale.
   - Action: deferred active firewall changes to avoid remote lockout. Implement nftables only after confirming physical or alternate access.

2. Docker socket access remains root-equivalent.
   - Evidence: `/var/run/docker.sock` is `root:docker`; containers including Traefik, Diun and Alloy mount it read-only for discovery; the `shreyws` user is in the `docker` group.
   - Impact: Docker API access can generally lead to host root compromise. A read-only bind mount protects the socket file from replacement, not the Docker API methods themselves.
   - Action: documented residual risk. Socket proxying is a future improvement only after endpoint requirements are tested for Traefik, Diun, Alloy and cAdvisor.

3. Host-inspection containers require broad host reads.
   - Evidence: cAdvisor mounts `/`, `/var/run`, `/sys`, `/var/lib/docker`; Node Exporter mounts `/` and uses host PID namespace.
   - Impact: compromise leaks host metadata and may increase blast radius.
   - Action: mounts are read-only where practical. Further hardening is limited by exporter requirements.

### Medium

1. Security updates were not automated.
   - Evidence: `unattended-upgrades` was not installed and multiple Debian packages had pending updates.
   - Action: added a conservative Debian security-update policy with automatic reboots disabled.

2. Kernel information exposure was partially permissive.
   - Evidence: `kernel.kptr_restrict=0`, `kernel.yama.ptrace_scope=0`.
   - Action: added managed sysctl hardening for kernel pointer visibility, dmesg, ptrace and protected link/file behavior.

3. Backup systemd units had little sandboxing.
   - Evidence: `systemd-analyze security` rated backup and backup-check services as unsafe.
   - Action: added low-risk service sandboxing while preserving Borg, staging, log and Docker dump access.

4. Several containers lacked basic runtime hardening.
   - Evidence: internal helper containers had no `no-new-privileges`, capability drops or PID limits.
   - Action: hardened low-risk helper services. High-access exporters were left unchanged to avoid breakage.

5. Floating container tags remain in some services.
   - Evidence: Grafana, Homepage and cAdvisor still use `latest`.
   - Action: documented as supply-chain follow-up. No image upgrades were performed during this audit.

6. Docker network segmentation is broad.
   - Evidence: most routed and observability services share `traefik_default`; Authentik PostgreSQL is isolated on `authentik_authentik_internal`.
   - Action: left unchanged in this pass because the platform is stable and route/auth changes are explicitly out of scope.

### Low

1. Avahi listens on all interfaces.
   - Action: deferred until the owner confirms whether LAN mDNS is useful.

2. CUPS is installed but only listens on loopback.
   - Action: no change.

3. Global HTTP security headers should be tested per application.
   - Action: no global CSP/HSTS changes were made. A restrictive policy could break Grafana or Authentik on the Tailscale hostname.

## Container Hardening Matrix

| Service | Key access | Action | Residual risk |
| --- | --- | --- | --- |
| Traefik | Docker socket; published 80/443 | No change | Docker socket discovery remains high-impact if compromised. |
| Authentik server/worker/proxy | Auth service and outpost | No change | Auth stack is core access path; avoid architecture changes without a focused Authentik maintenance window. |
| Authentik PostgreSQL | Internal database volume | No change | Isolated on Authentik internal network; logical dumps are backed up. |
| Grafana | Persistent data, provisioning | No change | Uses floating image tag; protected by Authentik. |
| Prometheus | Persistent TSDB, rules | No change | Internal scrape endpoint; protected browser route. |
| Alertmanager | Persistent state/config | No change | Internal only; existing local and Telegram receivers preserved. |
| alert-log-webhook | Writes local JSONL alerts | Added read-only root FS, tmpfs `/tmp`, `no-new-privileges`, dropped capabilities, PID limit | Can still write alert log path by design. |
| telegram-alert-webhook | Reads Telegram secret file | Added read-only root FS, tmpfs `/tmp`, `no-new-privileges`, dropped capabilities, PID limit | Delivery secret remains available inside this container only. |
| backup-metrics | Reads backup logs/state and writes textfile metrics | Added `no-new-privileges`, dropped capabilities, PID limit, and tmpfs `/tmp` | Reads backup state by design. A read-only root filesystem was tested but not kept because it blocked atomic textfile writes. |
| cAdvisor | Broad read-only host/Docker mounts | No change | Required for container metrics; high metadata exposure if compromised. |
| Node Exporter | Host rootfs read-only, host PID | No change | Required for host metrics. |
| Loki | Persistent log store | Added dropped capabilities, `no-new-privileges`, PID limit | Internal unauthenticated service reachable from Docker network. |
| Alloy | Docker socket, journald reads, Loki writes | Added dropped capabilities, `no-new-privileges`, PID limit | Docker socket and journal access remain privileged discovery paths. |
| Diun | Docker socket, local data | Added dropped capabilities, `no-new-privileges`, PID limit | Docker socket read access remains root-equivalent API exposure. |

## Secrets Assessment

Runtime secrets are stored outside Git under `/srv/shreyws/secrets` and `/etc/shreyws-backup`. Observed sensitive files use restrictive file permissions such as `0600`. The repository contains example environment files only.

Secrets are backed up by Borg, which is encrypted. This protects against casual backup-disk theft only if the Borg passphrase and repository key are not stored solely with the disk. Keep an independent offline copy of the Borg key/export and recovery passphrase.

Do not rotate secrets as part of routine hardening without a planned maintenance window. Candidates for future rotation include Authentik secret key/database password, Telegram bot token, and Borg passphrase if exposure is suspected.

## Patch Policy

Debian security updates are configured to install automatically with automatic reboot disabled. Container application updates remain controlled through Diun reporting and the reviewed `shreyws-container-update` workflow.

Manual follow-up remains necessary for:

- reboot-required kernel/library updates,
- non-security Debian stable updates,
- pinned container image updates,
- any service that needs post-update verification.

## Systemd Hardening

The backup, backup-check and SMART metrics units run as root because they need Borg repository access, system configuration reads, or SMART device inspection. Added hardening focuses on reducing accidental writes and privilege escalation:

- `NoNewPrivileges=true`
- `PrivateTmp=true`
- `ProtectSystem=full`
- `ProtectHome=read-only`
- explicit `ReadWritePaths=`
- `HOME=/var/lib/shreyws-backup/borg-home`, `BORG_CACHE_DIR=/var/cache/shreyws-backup/borg-cache` and `BORG_CONFIG_DIR=/var/lib/shreyws-backup/borg-config` so Borg does not need writable `/root`
- kernel/control-group protection
- no writable executable memory
- native syscall architecture
- restrictive `UMask=0077` for backup units

## Firewall Decision

No active firewall rules were changed during this audit. The system currently relies on service-level authentication, Tailscale, Docker-managed forwarding rules and upstream network policy. Because SSH and RDP are the remote recovery paths, enabling a deny-by-default host firewall remotely would be a lockout risk.

Recommended future firewall work, preferably with physical access available:

1. Confirm whether the current session is over Tailscale or LAN.
2. Confirm whether global IPv6 is reachable from outside the home network.
3. Allow established/related traffic, loopback, Tailscale, DHCP, essential ICMP, and explicit LAN management ports.
4. Preserve Docker-managed forwarding chains.
5. Test SSH and RDP from Tailscale and LAN before making the policy persistent.

## Recovery Access

If Authentik is unavailable, use Tailscale SSH to reach the host and either:

1. access Authentik directly through its unprotected `/authentik/` route if the service is healthy, or
2. temporarily remove the `authentik-forward-auth@docker` middleware from one affected router and recreate only that Compose project.

Do not protect `/authentik/` or `/outpost.goauthentik.io/` behind Authentik forward-auth.

## Rollback

Container hardening rollback:

```bash
cd /srv/shreyws/infra
git revert <hardening-commit>
cd compose/monitoring && docker compose up -d alert-log-webhook telegram-alert-webhook backup-metrics
cd ../logging && docker compose up -d loki alloy
cd ../diun && docker compose up -d diun
```

Systemd hardening rollback:

```bash
cd /srv/shreyws/infra
sudo cp systemd/shreyws-backup.service /etc/systemd/system/shreyws-backup.service
sudo cp systemd/shreyws-backup-check.service /etc/systemd/system/shreyws-backup-check.service
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/shreyws-backup.service /etc/systemd/system/shreyws-backup-check.service
```

Sysctl rollback:

```bash
sudo rm /etc/sysctl.d/99-shreyws-hardening.conf
sudo sysctl --system
```

Unattended-upgrades rollback:

```bash
sudo rm /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/52shreyws-unattended-upgrades
sudo apt purge unattended-upgrades
```

## Deferred Work

- Activate a tested nftables firewall policy with physical fallback available.
- Restrict SSH/RDP bind/access to Tailscale and trusted LAN if operationally acceptable.
- Continue reviewing image updates through Diun and the controlled update workflow. The previously floating Grafana, Homepage and cAdvisor images were pinned to their already-running versions on 2026-07-16.
- Evaluate a Docker socket proxy using measured API needs for each consumer.
- Split Docker networks further only after mapping required service-to-service flows.
- Decide whether Avahi/GNOME Remote Desktop listeners are still needed.
- Add security-focused LogQL panels for SSH/RDP/Auth failure trends without alerting on every failed login.
