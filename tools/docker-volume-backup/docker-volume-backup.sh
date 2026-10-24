#!/usr/bin/env bash
#
# docker-volume-backup.sh
#
# Back up one or more Docker named volumes to compressed tar archives with a
# configurable retention policy and SHA-256 integrity checksums.
#
# Works with plain `docker` (Engine) and `docker compose`. No external tools are
# required beyond docker, tar, gzip and sha256sum, all of which ship with any
# mainstream Linux distribution.
#
# Usage:
#   ./docker-volume-backup.sh [options] <volume> [<volume> ...]
#
# Options:
#   -d <dir>     Output directory for backups (default: ./backups)
#   -k <n>      Keep the last <n> backups per volume, delete older ones
#                (default: 7, 0 disables pruning)
#   -p <prefix>  File-name prefix string for every backup (default: vol)
#   -z <level>   gzip compression level 1-9 (default: 6)
#   -g <pgid>    Pause containers matching a Docker Compose project id while
#                backing up their volumes, then restart them. Use the same
#                value for all volumes in that compose stack.
#   -v          Verbose output
#   -n          Dry run: print what would happen without changing anything
#   -h          Show this help
#
# Exit codes:
#   0  success / clean help
#   1  usage error
#   2  docker not available or volume does not exist
#   3  backup step failed
#
# See README.md for examples and integration tips.
#
# License: AGPL-3.0

set -euo pipefail

readonly VERSION="1.0.0"
readonly DVB_IMAGE="busybox:stable"

BACKUP_DIR="./backups"
KEEP="7"
PREFIX="vol"
GZIP_LEVEL="6"
COMPOSE_PROJECT=""
VERBOSE="0"
DRY_RUN="0"

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

log() { [ "$VERBOSE" = "1" ] && echo "[docker-volume-backup] $*" >&2 || true; }
die() { echo "[docker-volume-backup] ERROR: $*" >&2; exit "${2:-1}"; }

while getopts ":d:k:p:z:g:vnh" opt; do
  case "$opt" in
    d) BACKUP_DIR="$OPTARG" ;;
    k) KEEP="$OPTARG" ;;
    p) PREFIX="$OPTARG" ;;
    z) GZIP_LEVEL="$OPTARG" ;;
    g) COMPOSE_PROJECT="$OPTARG" ;;
    v) VERBOSE="1" ;;
    n) DRY_RUN="1" ;;
    h) usage ;;
    \?) die "unknown option -$OPTARG" 1 ;;
    :)  die "option -$OPTARG requires an argument" 1 ;;
  esac
done
shift $((OPTIND - 1))

[ "$#" -ge 1 ] || die "no volume names given. Run '$0 -h' for help." 1
log "version ${VERSION}, dry-run=${DRY_RUN}"

# Validate user-supplied options before checking for external tools so that a
# clearly wrong flag is reported before a missing-dependency message.
re_is_int='^[0-9]+$'
[[ "$KEEP" =~ $re_is_int ]] || die "keep (-k) must be a non-negative integer" 1
[[ "$GZIP_LEVEL" =~ ^[1-9]$ ]] || die "gzip level (-z) must be between 1 and 9" 1

# We will always need gzip, tar and sha256sum locally because retention and the
# integrity sidecar use them. docker is only checked for when we actually run
# backup commands. In dry-run mode docker is not required so the preview works
# even on a host without Docker installed.
command -v gzip   >/dev/null 2>&1 || die "gzip not found in PATH" 2
command -v tar    >/dev/null 2>&1 || die "tar not found in PATH" 2
command -v sha256sum >/dev/null 2>&1 || die "sha256sum not found in PATH" 2

if [ "$DRY_RUN" = "0" ]; then
  command -v docker >/dev/null 2>&1 || die "docker not found in PATH" 2
fi

mkdir -p "$BACKUP_DIR"
[ -r "$BACKUP_DIR" ] && [ -w "$BACKUP_DIR" ] || die "cannot read/write backup dir '$BACKUP_DIR'" 1

timestamp="$(date +%Y%m%d-%H%M%S)"
tmp_root="${TMPDIR:-/tmp}"
tmpdir="$(mktemp -d "${tmp_root%/}/dvb.XXXXXX")"

pause_stack() {
  [ -z "$COMPOSE_PROJECT" ] && return 0
  log "pausing compose project '${COMPOSE_PROJECT}'"
  if [ "$DRY_RUN" = "1" ]; then return 0; fi
  # Pause from all compose labels belonging to this project id.
  docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
            --format '{{.Names}}' \
    | while read -r cname; do
        log "  docker pause ${cname} --quiet"
        docker pause "$cname" >/dev/null 2>&1 || true
      done
}

unpause_stack() {
  if [ -z "$COMPOSE_PROJECT" ]; then return 0; fi
  log "resuming compose project '${COMPOSE_PROJECT}'"
  if [ "$DRY_RUN" = "1" ]; then return 0; fi
  docker ps --filter "status=paused" \
            --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
            --format '{{.Names}}' \
    | while read -r cname; do
        log "  docker unpause ${cname}"
        docker unpause "$cname" >/dev/null 2>&1 || true
      done
}

# Always attempt to resume at the end, even on a later failure.
finish() { if [ -n "${COMPOSE_PROJECT:-}" ]; then unpause_stack; fi; rm -rf "$tmpdir"; }
trap 'finish' EXIT

pause_stack

vol_remaining=()
for vol in "$@"; do
  log "checking volume '${vol}'"
  if [ "$DRY_RUN" = "0" ]; then
    docker volume inspect "$vol" >/dev/null 2>&1 || {
      echo "[docker-volume-backup] WARNING: volume '${vol}' not found, skipping" >&2
      continue
    }
  fi
  vol_remaining+=("$vol")
done

if [ "${#vol_remaining[@]}" -eq 0 ]; then
  unpause_stack
  die "no valid volumes to back up" 1
fi

rc=0
for vol in "${vol_remaining[@]}"; do
  archive="${BACKUP_DIR}/${PREFIX}-${vol}-${timestamp}.tar.gz"
  temp_tar="${tmpdir}/${PREFIX}-${vol}-${timestamp}.tar.gz"

  log "archiving '${vol}' -> '${archive}'"

  if [ "$DRY_RUN" = "1" ]; then
    echo " [dry-run] docker run --rm -v ${vol}:/source:ro -v ${temp_tar}:/out.tar.gz ${DVB_IMAGE} tar c${GZIP_LEVEL}f /out.tar.gz -C /source ."
    echo " [dry-run] would write ${archive} + ${archive##*/}.sha256"
    continue
  fi

  if ! docker image inspect "$DVB_IMAGE" >/dev/null 2>&1; then
    log "image ${DVB_IMAGE} not present, pulling"
    if ! docker pull "$DVB_IMAGE" >/dev/null 2>&1; then
      echo "[docker-volume-backup] ERROR: cannot pull ${DVB_IMAGE} (offline?)" >&2
      rc=3
      continue
    fi
  fi

  # Mount the source volume read-only and the temp archive path read-write so
  # the tarball is written atomically (no partial files in BACKUP_DIR).
  if docker run --rm \
        -v "${vol}:/source:ro" \
        -v "${temp_tar}:/out.tar.gz" \
        "$DVB_IMAGE" \
        tar "c${GZIP_LEVEL}"f /out.tar.gz -C /source . ; then
    mv "$temp_tar" "$archive"
  else
    echo "[docker-volume-backup] ERROR: tar of volume '${vol}' failed" >&2
    rc=3
    continue
  fi

  size="$(du -h "$archive" | cut -f1)"
  sha="$(sha256sum "$archive" | awk '{print $1}')"
  echo "${sha}  ${archive##*/}" > "${archive}.sha256"
  echo " [ok] ${archive} (${size}, sha256 ${sha:0:12}...)"
done

# --- Retention -------------------------------------------------------------
if [ "$KEEP" -gt 0 ]; then
  for vol in "${vol_remaining[@]}"; do
    log "pruning to keep last ${KEEP} for '${vol}'"
    # List this volume's backups newest first.
    mapfile -t files < <(
      ls -1t "${BACKUP_DIR}/${PREFIX}-${vol}-"*.tar.gz 2>/dev/null || true
    )
    count="${#files[@]}"
    if [ "$count" -gt "$KEEP" ]; then
      # Files already newest first; drop the oldest by ignoring the head.
      for stale in "${files[@]:$KEEP}"; do
        [ -f "$stale" ] || continue
        # Best-effort prune of the matching checksum file too.
        checksum="${stale}.sha256"
        if [ "$DRY_RUN" = "1" ]; then
          echo " [dry-run] would delete ${stale##*/} + checksum"
          continue
        fi
        log "  deleting stale '${stale##*/}'"
        rm -f "$stale" "$checksum"
      done
    fi
  done
fi

if [ "$DRY_RUN" = "0" ]; then
  unpause_stack
fi

exit "$rc"
