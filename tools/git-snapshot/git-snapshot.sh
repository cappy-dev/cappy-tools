#!/usr/bin/env bash
#
# git-snapshot - Create timestamped archive snapshots of git repositories
#
# Usage: git-snapshot.sh [OPTIONS] [REPO_PATH]
#
# Creates .tar.gz archives of git repos with timestamps and optional
# commit info in the filename. Great for cron-based backups.
#

set -euo pipefail

VERSION="1.0.0"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Defaults ────────────────────────────────────────────────────────────────
OUTPUT_DIR=""
INCLUDE_DOTGIT=false
INCLUDE_UNTRACKED=true
COMPRESS=true
PREFIX=""
DRY_RUN=false
VERBOSE=false

# ── Help ────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
${BOLD}git-snapshot${RESET} v${VERSION} - Timestamped archive snapshots of git repos

${BOLD}USAGE${RESET}
    git-snapshot.sh [OPTIONS] [REPO_PATH]

${BOLD}DESCRIPTION${RESET}
    Creates a .tar.gz archive of the current (or specified) git repository.
    The archive filename includes a timestamp and optional branch/commit info.

    If REPO_PATH is omitted, the current directory is used.

${BOLD}OPTIONS${RESET}
    -o, --output DIR         Output directory for the archive (default: repo parent)
    -g, --include-dotgit     Include the .git directory in the archive
    -u, --skip-untracked     Exclude untracked files from the snapshot
    -n, --no-compress        Create an uncompressed .tar instead of .tar.gz
    -p, --prefix NAME        Custom prefix for the archive filename
        --dry-run            Print what would happen without creating anything
    -v, --verbose            Show detailed output
    -h, --help               Show this help message
        --version            Show version number

${BOLD}EXAMPLES${RESET}
    # Snapshot the current repo into its parent directory
    git-snapshot.sh

    # Snapshot a specific repo into ~/backups
    git-snapshot.sh -o ~/backups /path/to/my-repo

    # Snapshot including .git directory
    git-snapshot.sh --include-dotgit

    # Custom prefix for the archive name
    git-snapshot.sh --prefix myproject

    # Dry run to see what would be archived
    git-snapshot.sh --dry-run

${BOLD}ARCHIVE NAMING${RESET}
    Default:  <repo-name>-<branch>-<short-sha>-20260628-153000.tar.gz
    With --prefix: <prefix>-<branch>-<short-sha>-20260628-153000.tar.gz
    Dirty tree adds "-dirty" before the timestamp.

EOF
    exit 0
}

# ── Argument parsing ────────────────────────────────────────────────────────
REPO_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -g|--include-dotgit)
            INCLUDE_DOTGIT=true
            shift
            ;;
        -u|--skip-untracked)
            INCLUDE_UNTRACKED=false
            shift
            ;;
        -n|--no-compress)
            COMPRESS=false
            shift
            ;;
        -p|--prefix)
            PREFIX="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        --version)
            echo "git-snapshot v${VERSION}"
            exit 0
            ;;
        -*)
            echo -e "${RED}Error: Unknown option $1${RESET}" >&2
            exit 1
            ;;
        *)
            if [[ -n "$REPO_PATH" ]]; then
                echo -e "${RED}Error: Multiple repo paths specified${RESET}" >&2
                exit 1
            fi
            REPO_PATH="$1"
            shift
            ;;
    esac
done

# ── Resolve repo path ──────────────────────────────────────────────────────
if [[ -z "$REPO_PATH" ]]; then
    REPO_PATH="."
fi

REPO_PATH="$(cd "$REPO_PATH" && pwd)"

if [[ ! -d "$REPO_PATH/.git" ]]; then
    echo -e "${RED}Error: Not a git repository: $REPO_PATH${RESET}" >&2
    exit 1
fi

# ── Gather git info ─────────────────────────────────────────────────────────
REPO_NAME="$(basename "$REPO_PATH")"
BRANCH="$(git -C "$REPO_PATH" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")"
SHORT_SHA="$(git -C "$REPO_PATH" rev-parse --short HEAD 2>/dev/null || echo "unknown")"

# Check for uncommitted changes
if git -C "$REPO_PATH" diff --quiet HEAD 2>/dev/null && \
   git -C "$REPO_PATH" diff --cached --quiet 2>/dev/null; then
    DIRTY=""
else
    DIRTY="-dirty"
fi

# ── Build archive filename ──────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

if [[ -n "$PREFIX" ]]; then
    NAME_PREFIX="$PREFIX"
else
    NAME_PREFIX="$REPO_NAME"
fi

EXT="tar.gz"
if [[ "$COMPRESS" == false ]]; then
    EXT="tar"
fi

ARCHIVE_NAME="${NAME_PREFIX}-${BRANCH}-${SHORT_SHA}${DIRTY}-${TIMESTAMP}.${EXT}"

# ── Resolve output directory ───────────────────────────────────────────────
if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$(dirname "$REPO_PATH")"
fi

mkdir -p "$OUTPUT_DIR"
ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}"

# ── Determine what to include ──────────────────────────────────────────────
if [[ "$INCLUDE_DOTGIT" == true ]]; then
    # Archive the entire directory including .git
    if [[ "$DRY_RUN" == true ]]; then
        FILE_COUNT=$(find "$REPO_PATH" -not -path "$REPO_PATH" | wc -l)
        echo -e "${YELLOW}DRY${RESET} Would archive ${REPO_PATH} (${FILE_COUNT} entries) -> ${ARCHIVE_PATH}"
        exit 0
    fi

    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}Archiving full repo including .git${RESET}"
    fi

    if [[ "$COMPRESS" == true ]]; then
        tar -czf "$ARCHIVE_PATH" -C "$(dirname "$REPO_PATH")" "$(basename "$REPO_PATH")"
    else
        tar -cf "$ARCHIVE_PATH" -C "$(dirname "$REPO_PATH")" "$(basename "$REPO_PATH")"
    fi
else
    # Use git archive for tracked files, then optionally add untracked
    if [[ "$DRY_RUN" == true ]]; then
        TRACKED=$(git -C "$REPO_PATH" ls-files | wc -l)
        MSG="Would archive ${TRACKED} tracked files"
        if [[ "$INCLUDE_UNTRACKED" == true ]]; then
            UNTRACKED=$(git -C "$REPO_PATH" ls-files --others --exclude-standard | wc -l)
            MSG+=" and ${UNTRACKED} untracked files"
        fi
        MSG+=" -> ${ARCHIVE_PATH}"
        echo -e "${YELLOW}DRY${RESET} ${MSG}"
        exit 0
    fi

    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}Archiving tracked files${RESET}"
    fi

    if [[ "$INCLUDE_UNTRACKED" == true ]]; then
        # Combine tracked and untracked files into one archive
        UNTRACKED_FILES=$(git -C "$REPO_PATH" ls-files --others --exclude-standard)

        if [[ -z "$UNTRACKED_FILES" ]]; then
            # Only tracked files
            if [[ "$COMPRESS" == true ]]; then
                git -C "$REPO_PATH" archive --format=tar.gz --prefix="${REPO_NAME}/" -o "$ARCHIVE_PATH" HEAD
            else
                git -C "$REPO_PATH" archive --format=tar --prefix="${REPO_NAME}/" -o "$ARCHIVE_PATH" HEAD
            fi
        else
            # Tracked + untracked: create tar, then append untracked
            TEMP_TAR=$(mktemp /tmp/git-snapshot-XXXXXX.tar)

            # Step 1: git archive for tracked files
            git -C "$REPO_PATH" archive --format=tar --prefix="${REPO_NAME}/" -o "$TEMP_TAR" HEAD

            # Step 2: append untracked files
            while IFS= read -r file; do
                tar -rf "$TEMP_TAR" -C "$REPO_PATH" --transform="s/^/${REPO_NAME}\//" "$file"
            done <<< "$UNTRACKED_FILES"

            # Step 3: compress if needed
            if [[ "$COMPRESS" == true ]]; then
                gzip -c "$TEMP_TAR" > "$ARCHIVE_PATH"
                rm -f "$TEMP_TAR"
            else
                mv "$TEMP_TAR" "$ARCHIVE_PATH"
            fi
        fi
    else
        # Only tracked files
        if [[ "$COMPRESS" == true ]]; then
            git -C "$REPO_PATH" archive --format=tar.gz --prefix="${REPO_NAME}/" -o "$ARCHIVE_PATH" HEAD
        else
            git -C "$REPO_PATH" archive --format=tar --prefix="${REPO_NAME}/" -o "$ARCHIVE_PATH" HEAD
        fi
    fi
fi

# ── Report ──────────────────────────────────────────────────────────────────
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)

echo -e "${GREEN}Snapshot created:${RESET} ${ARCHIVE_PATH}"
echo -e "  ${BOLD}Repo:${RESET}    ${REPO_PATH}"
echo -e "  ${BOLD}Branch:${RESET}  ${BRANCH}"
echo -e "  ${BOLD}Commit:${RESET}  ${SHORT_SHA}${DIRTY}"
echo -e "  ${BOLD}Size:${RESET}    ${ARCHIVE_SIZE}"
