#!/bin/sh
set -eu

LOG_DIR=/logs
OUT_DIR=/textfile
OUT_FILE="$OUT_DIR/shreyws_backup.prom"
TMP_FILE="$OUT_FILE.$$"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"

unit_to_bytes() {
  value="$1"
  unit="$2"
  awk -v v="$value" -v u="$unit" 'BEGIN {
    mult = 1
    if (u == "kB" || u == "KB") mult = 1000
    else if (u == "MB") mult = 1000 * 1000
    else if (u == "GB") mult = 1000 * 1000 * 1000
    else if (u == "KiB") mult = 1024
    else if (u == "MiB") mult = 1024 * 1024
    else if (u == "GiB") mult = 1024 * 1024 * 1024
    printf "%.0f", v * mult
  }'
}

emit_metrics() {
  backup_log="$LOG_DIR/backup.log"
  check_log="$LOG_DIR/check.log"
  now="$(date +%s)"

  last_success="0"
  backup_status="0"
  repo_check_status="0"
  verify_status="0"
  duration="0"
  original_bytes="0"
  compressed_bytes="0"
  deduplicated_bytes="0"
  reboot_required="0"

  if [ -r "$backup_log" ]; then
    last_success_line="$(grep 'Backup completed' "$backup_log" | tail -1 || true)"
    size_line="$(grep 'This archive:' "$backup_log" | tail -1 || true)"
    duration_line="$(grep 'Duration:' "$backup_log" | tail -1 || true)"

    if [ -n "$last_success_line" ]; then
      last_success="$(stat -c %Y "$backup_log" 2>/dev/null || echo 0)"
      backup_status="1"
    fi
    if grep -q 'Running repository-only consistency check' "$backup_log" && grep -q 'Backup completed' "$backup_log"; then
      repo_check_status="1"
    fi
    if [ -n "$duration_line" ]; then
      duration="$(printf '%s\n' "$duration_line" | awk '{print int($2 + 0.5)}')"
    fi
    if [ -n "$size_line" ]; then
      set -- $size_line
      if [ "$#" -ge 8 ]; then
        original_bytes="$(unit_to_bytes "$3" "$4")"
        compressed_bytes="$(unit_to_bytes "$5" "$6")"
        deduplicated_bytes="$(unit_to_bytes "$7" "$8")"
      fi
    fi
  fi

  if [ -r "$check_log" ] && grep -q 'Repository verification completed' "$check_log"; then
    verify_status="1"
  fi
  if [ -e /host/run/reboot-required ]; then
    reboot_required="1"
  fi

  age="0"
  if [ "$last_success" -gt 0 ]; then
    age="$((now - last_success))"
  fi

  {
    echo '# HELP shreyws_backup_last_success_timestamp_seconds Unix timestamp of the last completed ShreyWS Borg backup.'
    echo '# TYPE shreyws_backup_last_success_timestamp_seconds gauge'
    echo "shreyws_backup_last_success_timestamp_seconds $last_success"
    echo '# HELP shreyws_backup_age_seconds Seconds since the last completed ShreyWS Borg backup.'
    echo '# TYPE shreyws_backup_age_seconds gauge'
    echo "shreyws_backup_age_seconds $age"
    echo '# HELP shreyws_backup_duration_seconds Duration of the last completed ShreyWS Borg backup.'
    echo '# TYPE shreyws_backup_duration_seconds gauge'
    echo "shreyws_backup_duration_seconds $duration"
    echo '# HELP shreyws_backup_last_success Last Borg backup status, 1 for success.'
    echo '# TYPE shreyws_backup_last_success gauge'
    echo "shreyws_backup_last_success $backup_status"
    echo '# HELP shreyws_backup_repository_check_success Last repository-only check status, 1 for success.'
    echo '# TYPE shreyws_backup_repository_check_success gauge'
    echo "shreyws_backup_repository_check_success $repo_check_status"
    echo '# HELP shreyws_backup_verify_data_success Last full verify-data check status, 1 for success.'
    echo '# TYPE shreyws_backup_verify_data_success gauge'
    echo "shreyws_backup_verify_data_success $verify_status"
    echo '# HELP shreyws_backup_archive_bytes Last Borg archive size by size type.'
    echo '# TYPE shreyws_backup_archive_bytes gauge'
    echo "shreyws_backup_archive_bytes{type=\"original\"} $original_bytes"
    echo "shreyws_backup_archive_bytes{type=\"compressed\"} $compressed_bytes"
    echo "shreyws_backup_archive_bytes{type=\"deduplicated\"} $deduplicated_bytes"
    echo '# HELP shreyws_reboot_required Whether the host has /run/reboot-required present.'
    echo '# TYPE shreyws_reboot_required gauge'
    echo "shreyws_reboot_required $reboot_required"
  } > "$TMP_FILE"
  mv "$TMP_FILE" "$OUT_FILE"
}

mkdir -p "$OUT_DIR"
while true; do
  emit_metrics
  sleep "$INTERVAL_SECONDS"
done
