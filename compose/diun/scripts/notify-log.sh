#!/bin/sh
set -eu

log_file="/data/notifications.log"

{
  printf '%s\n' '---'
  date -Is
  env | sort | grep '^DIUN_' | sed -E 's/(TOKEN|PASSWORD|SECRET|KEY)=.*/\1=REDACTED/I'
} >> "$log_file"
