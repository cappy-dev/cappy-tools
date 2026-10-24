#!/usr/bin/env bash
# eden-launcher.sh
#
# Downloads and launches the latest Eden nightly Linux amd64 PGO AppImage.
# Uses the official update API at nightly.eden-emu.dev to discover new
# releases, and caches downloads so we only fetch when a new version
# appears.
#
# Usage:
#   ./eden-launcher.sh           # download (if needed) and launch
#   ./eden-launcher.sh --check    # only check for / print the latest version
#   ./eden-launcher.sh --force    # re-download even if cached
#   ./eden-launcher.sh --clean    # delete the cache and exit
#   ./eden-launcher.sh --help     # show this help
#
# Requires: curl, jq, and a desktop/Linux environment that can run AppImages.
# Grab jq if you don't have it:  sudo apt install jq  /  sudo dnf install jq
#
# Author: cappy-dev (authored via Hermes Agent)
# Source: https://git.eden-emu.dev/eden-emu/eden/src/commit/89004124a55a5ce8c45a90dcb60afd8a107238c3/CMakeModules/GenerateSCMRev.cmake

set -euo pipefail

API_URL="https://nightly.eden-emu.dev/latest/release.json"
CACHE_DIR="${HOME}/.cache/eden-launcher"
STATE_FILE="${CACHE_DIR}/current_version"
ASSET_GLOB="Eden-Linux-*-amd64-clang-pgo.AppImage"
ASSET_GLOB_REGEX='Eden-Linux-[^/]+-amd64-clang-pgo\.AppImage$'

# --- helpers ----------------------------------------------------------------

cyan()   { printf '\033[36m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red()    { printf '\033[31m%s\033[0m\n' "$*"; }
die()    { red "error: $*" >&2; exit 1; }

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: '$1' (install it and retry)"
}

# Fetch the latest release metadata from the API. Prints the JSON to stdout.
fetch_release_json() {
  curl -fsSL "$API_URL"
}

# Extract the AppImage asset URL for the amd64 clang pgo build from the
# release JSON on stdin. Prints the URL to stdout (empty on no match).
extract_asset_url() {
  jq -r --arg re "$ASSET_GLOB_REGEX" \
    '.assets[] | select(test($re))' 2>/dev/null
}

# Pretty-print what we know about the latest release for --check.
show_latest() {
  local json
  json="$(fetch_release_json)" || die "failed to fetch $API_URL"
  local tag name url
  tag="$(printf '%s' "$json" | jq -r '.tag_name')"
  name="$(printf '%s' "$json" | jq -r '.name')"
  url="$(printf '%s' "$json" | extract_asset_url)"
  [ -n "$url" ] || die "no Linux amd64 PGO AppImage asset found in the latest release"

  cyan "Eden nightly release info"
  printf '  tag:   %s\n' "$tag"
  printf '  name:  %s\n' "$name"
  printf '  asset: %s\n' "$url"
}

# Make sure the cache directory exists and is private.
ensure_cache_dir() {
  mkdir -p "$CACHE_DIR"
  chmod 700 "$CACHE_DIR" 2>/dev/null || true
}

# Read the currently-installed version tag from the state file (empty if none).
installed_version() {
  [ -f "$STATE_FILE" ] && cat "$STATE_FILE" 2>/dev/null || true
}

# Download (or re-download) the asset at $1 into $CACHE_DIR, make it
# executable, and record the tag $2 in the state file.
install_asset() {
  local url="$1"
  local tag="$2"
  local filename asset_path
  filename="$(basename "$url")"
  asset_path="${CACHE_DIR}/${filename}"

  yellow "downloading: $url"
  # Atomic-ish: download to a .partial, then rename on success.
  curl -fSL --progress-bar -o "${asset_path}.partial" "$url"
  mv -f "${asset_path}.partial" "$asset_path"
  chmod +x "$asset_path"

  # Clean up older AppImages that aren't the new one.
  local old oldname
  for old in "$CACHE_DIR"/Eden-Linux-*-amd64-clang-pgo.AppImage; do
    [ -e "$old" ] || continue
    [ "$old" = "$asset_path" ] && continue
    rm -f "$old"
  done

  printf '%s' "$tag" > "$STATE_FILE"
  green "installed: $filename"
}

# Find the cached AppImage that matches the current state. Prints path or empty.
cached_appimage_path() {
  local tag
  tag="$(installed_version)"
  [ -n "$tag" ] || return 0
  local match
  match="$(ls "${CACHE_DIR}"/${ASSET_GLOB} 2>/dev/null | head -n1 || true)"
  [ -n "$match" ] && printf '%s' "$match" || true
}

# --- main -------------------------------------------------------------------

main() {
  local force=0 check_only=0 clean=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --help|-h) usage ;;
      --check)   check_only=1 ;;
      --force)   force=1 ;;
      --clean)   clean=1 ;;
      *) die "unknown argument: '$1' (try --help)" ;;
    esac
    shift
  done

  require_cmd curl
  require_cmd jq

  if [ $clean -eq 1 ]; then
    rm -rf "$CACHE_DIR"
    green "cache cleared: $CACHE_DIR"
    exit 0
  fi

  if [ $check_only -eq 1 ]; then
    show_latest
    exit 0
  fi

  ensure_cache_dir

  cyan "fetching latest Eden nightly release info..."
  local json tag url
  json="$(fetch_release_json)" || die "failed to fetch $API_URL"
  tag="$(printf '%s' "$json" | jq -r '.tag_name')"
  url="$(printf '%s' "$json" | extract_asset_url)"
  [ -n "$url" ] || die "no Linux amd64 PGO AppImage asset found in release '$tag'"

  local current
  current="$(installed_version)"
  local cached_path
  cached_path="$(cached_appimage_path)"

  if [ $force -eq 0 ] && [ -n "$cached_path" ] && [ "$current" = "$tag" ]; then
    green "already up to date: $tag"
    cyan "launching: $cached_path"
    exec "$cached_path"
  fi

  if [ -n "$current" ] && [ "$current" != "$tag" ]; then
    yellow "new version available: $current -> $tag"
  else
    yellow "no cached version found; downloading $tag"
  fi

  install_asset "$url" "$tag"
  cached_path="${CACHE_DIR}/$(basename "$url")"

  cyan "launching: $cached_path"
  exec "$cached_path"
}

main "$@"
