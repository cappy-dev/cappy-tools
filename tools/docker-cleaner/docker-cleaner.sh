#!/usr/bin/env bash
#
# docker-cleaner.sh - reclaim disk space from unused Docker objects
#
# Safely prunes stopped containers, dangling images, unused volumes, and
# orphaned networks. Runs a dry-run by default so you can preview what would
# be removed before anything is deleted.
#
# Usage:
#   ./docker-cleaner.sh                 # dry-run, show what would be removed
#   ./docker-cleaner.sh --apply          # actually delete the unused objects
#   ./docker-cleaner.sh --apply --all    # also prune unused volumes & everything
#                                      # NOT referenced by a running container
#   ./docker-cleaner.sh --keep-images    # skip image pruning (containers/volumes/networks)
#   ./docker-cleaner.sh --help
#
# Zero dependencies beyond bash, docker, and standard coreutils.
# Works on Linux and macOS (GNU and BSD flavors of awk/sed).
#
set -euo pipefail

PROG="docker-cleaner.sh"
VERSION="1.0.0"

# ---- defaults ---------------------------------------------------------------
APPLY=0
ALL=0
KEEP_IMAGES=0
QUIET=0

# ---- helpers ----------------------------------------------------------------
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

usage() {
  cat <<'EOF'
docker-cleaner.sh - reclaim disk space from unused Docker objects

USAGE
  docker-cleaner.sh [--apply] [--all] [--keep-images] [--quiet] [--help]

OPTIONS
  --apply         Actually delete objects (default is a safe dry-run).
  --all           Prune aggressively: unused volumes and everything not
                  referenced by a running container. Implies --apply is needed
                  to take effect.
  --keep-images   Skip image pruning. Still prune containers, volumes,
                  and networks.
  --quiet         Suppress non-error output.
  --help          Show this help.
  --version       Show version and exit.

EXAMPLES
  # preview only (nothing is deleted)
  ./docker-cleaner.sh

  # delete unused containers, dangling images, and orphan networks
  ./docker-cleaner.sh --apply

  # aggressive: also remove unused volumes and unreferenced images
  ./docker-cleaner.sh --apply --all

  # free volume/network space but leave images untouched
  ./docker-cleaner.sh --apply --keep-images

NOTES
  - A dry-run never deletes anything. Use --apply to commit changes.
  --all also removes unused volumes, which can be destructive for data
  that is not mounted by a running container. Review the dry-run first.
EOF
  exit 0
}

# ---- argparse ---------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --all)   ALL=1; shift ;;
    --keep-images) KEEP_IMAGES=1; shift ;;
    --quiet) QUIET=1; shift ;;
    --help|-h) usage ;;
    --version) echo "$PROG $VERSION"; exit 0 ;;
    *) echo "$PROG: unknown option '$1' (try --help)" >&2; exit 2 ;;
  esac
done

# ---- sanity checks ----------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "$PROG: 'docker' command not found. Install Docker and try again." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "$PROG: cannot reach the Docker daemon. Is the docker service running?" >&2
  echo "$PROG: try 'sudo systemctl start docker' or check your permissions/user group." >&2
  exit 1
fi

# ---- human-readable byte sizes ---------------------------------------------
human() {
  # converts a byte count (first arg) into a friendly string.
  local bytes="${1:-0}"
  local units=("B" "KB" "MB" "GB" "TB" "PB")
  local i=0
  local size="$bytes"
  while [ "${size%.*}" -ge 1024 ] && [ $i -lt ${#units[@]} ]; do
    size=$(awk -v s="$size" 'BEGIN { printf "%.2f", s / 1024 }')
    i=$((i + 1))
  done
  printf "%.1f %s" "$size" "${units[$i]}"
}

# ---- disk summary -----------------------------------------------------------
disk_summary() {
  # docker system df returns SIZE and RECLAIMABLE columns. We surface the
  # total reclaimable space so you know what is on the table.
  local df_out
  df_out="$(docker system df --format '{{.Type}}|{{.TotalCount}}|{{.Size}}|{{.Reclaimable}}')"
  if [ "$QUIET" -ne 1 ]; then
    bold "Docker disk usage summary"
    dim "type                 count      size        reclaimable"
    echo "$df_out" | while IFS='|' read -r type count size reclaim; do
      printf '%-20s %-10s %-11s %-11s\n' "$type" "$count" "$size" "$reclaim"
    done
    echo
  fi
}

# ---- report a prune step ----------------------------------------------------
step() {
  # $1 = label, $2 = command (printed or, when APPLY=1, executed)
  local label="$1"; shift
  local mode
  if [ "$APPLY" -eq 1 ]; then mode="APPLY"; else mode="DRY-RUN"; fi
  if [ "$QUIET" -ne 1 ]; then
    printf '\n[%s] %s\n' "$mode" "$label"
    dim "command: $*"
  fi
  if [ "$APPLY" -eq 1 ]; then
    if [ "$QUIET" -eq 1 ]; then
      "$@" >/dev/null 2>&1 || true
    else
      "$@" 2>&1 || true
    fi
  fi
}

# ---- main -------------------------------------------------------------------
if [ "$QUIET" -ne 1 ]; then
  bold "== $PROG v$VERSION =="
  if [ "$APPLY" -eq 0 ]; then
    yellow "DRY-RUN MODE (nothing will be deleted). Pass --apply to commit."
  else
    green "APPLY MODE: unused objects WILL be deleted."
    [ "$ALL" -eq 1 ] && yellow "--all: also removing unused volumes and unreferenced images."
    [ "$KEEP_IMAGES" -eq 1 ] && yellow "--keep-images: pruning containers/volumes/networks only."
  fi
fi

disk_summary

# 1) stopped containers
step "prune stopped containers" \
  docker container prune --force

# 2) unreachable / build cache (always useful)
step "prune build cache" \
  docker builder prune --force

# 3) images
if [ "$KEEP_IMAGES" -eq 0 ]; then
  if [ "$ALL" -eq 1 ]; then
    # --all also drops images not referenced by a running container
    step "prune unused images (all, including tagged)" \
      docker image prune --all --force
  else
    # only dangling (untagged) images
    step "prune dangling images" \
      docker image prune --force
  fi
else
  if [ "$QUIET" -ne 1 ]; then
    echo; dim "skipping image pruning (--keep-images)"; fi
fi

# 4) networks (only removes networks not referenced by containers)
step "prune unused networks" \
  docker network prune --force

# 5) volumes (only with --all, since this can be destructive)
if [ "$ALL" -eq 1 ]; then
  step "prune unused volumes (DESTRUCTIVE: data in unmounted volumes is lost)" \
    docker volume prune --force
else
  if [ "$QUIET" -ne 1 ]; then
    echo; dim "skipping volume pruning (use --all to include volumes)"; fi
fi

# ---- final report -----------------------------------------------------------
if [ "$QUIET" -ne 1 ]; then
  echo
  if [ "$APPLY" -eq 1 ]; then
    green "Done. Reclaimed space has been freed."
    echo "After (current disk usage):"
    docker system df
  else
    green "Dry-run complete. Nothing was deleted."
    echo "Inspect the commands above; rerun with --apply to actually free the space."
    echo "Add --all to also remove unused volumes and unreferenced images."
  fi
  echo
  dim "Tip: large dangling build caches are a common culprit. 'docker builder prune --all' clears them."
  echo
fi
