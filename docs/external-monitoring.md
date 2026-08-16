# External ShreyWS Outage Monitor

ShreyWS is checked independently from the workstation `sm-gram14` over
Tailscale. This catches outages where the server and its own Prometheus and
Alertmanager stack are unavailable together.

## Check

The user-level systemd timer runs once per minute and requires both:

- ICMP reachability to the ShreyWS Tailscale address `100.108.162.19`
- an HTTPS response in the `200` through `399` range from
  `https://shreyws.tail1591fa.ts.net/`

The HTTPS request uses the workstation's normal CA trust store and hostname
verification. An expired, self-signed, or mismatched certificate therefore
counts as a failed check.

An outage notification is sent after three consecutive failed checks. A
recovery notification is sent after two consecutive successful checks. State
is persisted locally so a single transient failure or recovery does not page.

## Workstation Files

```text
~/.local/bin/shreyws-outage-monitor
~/.config/systemd/user/shreyws-outage-monitor.service
~/.config/systemd/user/shreyws-outage-monitor.timer
~/.config/shreyws-outage-monitor/telegram.env
~/.local/state/shreyws-outage-monitor/state.json
```

The Telegram environment file is mode `0600`; its parent directory is mode
`0700`. Credentials are not stored in this repository.

Useful commands on `sm-gram14`:

```bash
systemctl --user status shreyws-outage-monitor.timer
journalctl --user -u shreyws-outage-monitor.service
systemctl --user start shreyws-outage-monitor.service
```

## Availability Limitation

The monitor only works while `sm-gram14` is powered on, awake, connected to the
network and Tailscale, and its user systemd manager is running. User lingering
is currently disabled, so logging out can stop the monitor. If desired, enable
logged-out operation locally with administrator access:

```bash
sudo loginctl enable-linger shrey
```

This does not make the monitor independent of workstation sleep or power loss.

## Verification

On 2026-08-16, the script's notification self-test delivered both outage and
recovery messages. After installing the Tailscale/Let's Encrypt certificate, a
live timer invocation passed with Tailscale ping available, trusted HTTPS, and
status `302`.

On the same date, a temporary Prometheus alert verified the complete server-side
pipeline through Alertmanager, the local JSONL webhook, and Telegram. The test
rule was removed after its firing and resolved notifications were delivered.
