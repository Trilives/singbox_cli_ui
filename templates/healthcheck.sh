#!/usr/bin/env bash

# Watchdog probe for the sing-box service.
#
# sing-box can enter a "soft death": the process stays alive but, after the
# uplink interface appears late at boot or drops/changes, auto_detect_interface
# is stuck on "missing default interface" and every outbound (including DNS)
# times out. systemd's Restart=on-failure never fires because the process does
# not exit. This script detects that state and restarts the service.
#
# Decision logic (deliberately conservative to avoid restart storms):
#   1. If the host has no usable uplink at all, do nothing -- a restart cannot
#      fix a missing network, and thrashing would only add noise.
#   2. If an uplink exists but proxying through the mixed inbound fails after a
#      few tries, sing-box is stuck -> restart it.
#   3. Skip the restart if the service has just (re)started, so we don't kill an
#      instance that is still initialising.

set -uo pipefail

SERVICE_NAME="${SERVICE_NAME:-sing-box}"
# Underlying tun device created by sing-box; excluded from uplink detection so
# the tun itself is never mistaken for real connectivity.
TUN_DEV="${TUN_DEV:-singbox}"
# Mixed/HTTP proxy inbound to probe through. Auto-detected from the runtime
# config when possible, with a sane fallback.
PROXY_ADDR="${PROXY_ADDR:-}"
PROBE_URL="${PROBE_URL:-http://connectivitycheck.gstatic.com/generate_204}"
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-3}"
PROBE_TIMEOUT="${PROBE_TIMEOUT:-8}"
PROBE_GAP="${PROBE_GAP:-4}"
# Do not restart an instance younger than this many seconds (let it settle).
MIN_UPTIME="${MIN_UPTIME:-90}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Resolve the proxy address from the live runtime config if not provided.
if [[ -z "${PROXY_ADDR}" ]]; then
  runtime_config="/etc/sing-box/${SERVICE_NAME}.json"
  if [[ -r "${runtime_config}" ]] && command -v python3 >/dev/null 2>&1; then
    PROXY_ADDR="$(python3 - "${runtime_config}" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for inbound in data.get("inbounds", []):
    if inbound.get("type") in ("mixed", "http"):
        host = inbound.get("listen") or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = inbound.get("listen_port")
        if port:
            print(f"{host}:{port}")
            break
PY
)"
  fi
fi
PROXY_ADDR="${PROXY_ADDR:-127.0.0.1:7890}"

# True if a default route exists via something other than the sing-box tun,
# i.e. the host actually has an uplink to (re)bind to.
have_uplink() {
  local dev
  while read -r dev; do
    [[ -n "${dev}" && "${dev}" != "${TUN_DEV}" ]] && return 0
  done < <(ip route show default 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") print $(i+1)}')
  return 1
}

# True if a request actually completes through the proxy within the retries.
proxy_works() {
  local i
  for ((i = 1; i <= PROBE_ATTEMPTS; i++)); do
    if curl -fsS -o /dev/null -m "${PROBE_TIMEOUT}" \
        -x "http://${PROXY_ADDR}" "${PROBE_URL}"; then
      return 0
    fi
    [[ ${i} -lt ${PROBE_ATTEMPTS} ]] && sleep "${PROBE_GAP}"
  done
  return 1
}

service_uptime_seconds() {
  local enter enter_s now_s
  enter="$(systemctl show -p ActiveEnterTimestamp --value "${SERVICE_NAME}" 2>/dev/null)"
  [[ -z "${enter}" ]] && { echo 999999; return; }
  enter_s="$(date -d "${enter}" +%s 2>/dev/null || echo 0)"
  now_s="$(date +%s)"
  echo $(( now_s - enter_s ))
}

main() {
  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "${SERVICE_NAME} is not active; leaving it to systemd."
    return 0
  fi

  if ! have_uplink; then
    log "No uplink (only ${TUN_DEV}/none); a restart cannot help. Skipping."
    return 0
  fi

  if proxy_works; then
    return 0
  fi

  local uptime
  uptime="$(service_uptime_seconds)"
  if [[ "${uptime}" -lt "${MIN_UPTIME}" ]]; then
    log "Proxy probe failed but ${SERVICE_NAME} is only ${uptime}s old; letting it settle."
    return 0
  fi

  log "Uplink present but proxy ${PROXY_ADDR} dead after ${PROBE_ATTEMPTS} tries; restarting ${SERVICE_NAME}."
  systemctl restart "${SERVICE_NAME}"
}

main "$@"
