# Weekly Operations Digest

`shreyws-weekly-digest.timer` sends a compact Telegram status summary each
Sunday at approximately 09:00 Europe/Zurich. A randomized delay of up to 20
minutes avoids scheduling every maintenance task at exactly the same time.

The digest reports host uptime, container and systemd health, firewall state,
disk utilization, backup age, restore-drill status, pending Debian package
updates, Git drift, and active Alertmanager alerts.

Runtime files:

- `/usr/local/sbin/shreyws-weekly-digest`
- `/etc/systemd/system/shreyws-weekly-digest.service`
- `/etc/systemd/system/shreyws-weekly-digest.timer`

The script reads the existing root-protected Telegram environment file and
does not print credentials. Preview without sending:

```bash
sudo /usr/local/sbin/shreyws-weekly-digest --dry-run
```

Send immediately:

```bash
sudo systemctl start shreyws-weekly-digest.service
```

Inspect scheduling and delivery:

```bash
systemctl list-timers shreyws-weekly-digest.timer
journalctl -u shreyws-weekly-digest.service
```
