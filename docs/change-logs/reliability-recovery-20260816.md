# Reliability Recovery - 2026-08-16

## Context

After a host reboot, several Compose services were absent and Node Exporter had
an empty textfile collector despite valid backup and SMART metric files on the
host. The pilot workload had also been retired but remained configured as a
Prometheus target.

## Changes

- Restored the monitoring, logging, alerting and Diun Compose projects from the
  existing pinned definitions.
- Recreated Node Exporter to replace a stale bind mount created before `/srv`
  finished mounting.
- Added `systemd/docker.service.d/10-require-srv.conf` with
  `RequiresMountsFor=/srv` and `After=srv.mount` so future Docker starts cannot
  precede the data mount.
- Replaced unstable SMART `/dev/sdX` assignments with stable
  `/dev/disk/by-id` paths while keeping serial numbers out of Prometheus labels.
- Removed the retired pilot scrape target, alert rules and Prometheus network
  attachment.

## Verification

- Validated all affected Compose configuration.
- Validated Prometheus configuration and 42 active rules with `promtool`.
- Ran the installed SMART collector successfully and confirmed the system,
  `/srv` and backup disks map to the correct logical labels.
- Confirmed the degraded ST500DM002 drive is reported as `backup-disk`, with
  152 reallocated sectors and 8 offline-uncorrectable sectors.
- Confirmed Docker's effective systemd dependencies include `srv.mount` in both
  `Requires` and `After`.
- Recreated only Prometheus after removing its pilot network dependency.
- Confirmed current Loki and Alloy ingestion is error-free after the one-time
  historical log replay.

Docker was not restarted during this change. The new mount ordering is loaded
and will govern the next Docker start or host reboot.
