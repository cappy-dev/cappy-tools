#!/usr/bin/env bash
# cross-shell-prompt: a clean, informative shell prompt for bash and zsh
# https://github.com/cappy-dev/cappy-tools/tree/main/tools/cross-shell-prompt
#
# Zero dependencies. Uses only built-in shell features.
# Features:
#   - Last command exit code indicator (green dot on success, red on failure)
#   - Current git branch (when inside a git repo)
#   - Abbreviated path (leading ~ for home, replaced middle dirs with ...)
#   - Optional hostname (useful for SSH sessions)
#   - Color or plain output
#
# Suggested install: source this file from your ~/.bashrc or ~/.zshrc
#   source /path/to/prompt.sh
#
# Or copy the two helper functions (__csp_prompt and csp_path_short)
# directly into your shell config.

# ---------------------------------------------------------------------------
# Colors (ANSI). Override any of these via environment variables if needed.
# ---------------------------------------------------------------------------
: "${CSP_COLOR_DIR:=$'\033[1;36m'}"      # bold cyan
: "${CSP_COLOR_HOST:=$'\033[0;35m'}"     # magenta
: "${CSP_COLOR_GIT:=$'\033[0;33m'}"      # yellow
: "${CSP_COLOR_OK:=$'\033[1;32m'}"       # bold green
: "${CSP_COLOR_FAIL:=$'\033[1;31m'}"     # bold red
: "${CSP_COLOR_RESET:=$'\033[0m'}"

# ---------------------------------------------------------------------------
# csp_path_short PATH
# Abbreviate a directory path: leading ~, then collapse intermediate dirs.
# Examples:
#   /home/user/projects/myapp   ->  ~/p/myapp
#   /etc/systemd/system         ->  /e/s/system
#   ~/code/very/deep/folder     ->  ~/c/v/d/folder
# ---------------------------------------------------------------------------
csp_path_short() {
  local p="$1"
  local home_short=""
  local prefix=""
  local result=""

  # Handle root specially.
  if [ "$p" = "/" ]; then
    printf '/'
    return
  fi

  # Replace $HOME with ~ if the path starts with it.
  case "$p" in
    "$HOME")
      printf '~'
      return
      ;;
    "$HOME"/*)
      home_short='~'
      p="${p#"$HOME"/}"
      prefix="${home_short}/"
      ;;
  esac

  # Now p has no leading $HOME. Split on '/'.
  local IFS='/'
  # shellcheck disable=SC2086
  set -- $p

  local total=$#
  local idx=1
  for part in "$@"; do
    if [ "$idx" -eq "$total" ]; then
      # Keep the final directory element in full.
      result="${result}${part}"
    else
      # First character of the intermediate dir (or empty if empty).
      local initial="${part%"${part#?}"}"
      result="${result}${initial}/"
    fi
    idx=$((idx + 1))
  done

  printf '%s' "${prefix}${result}"
}

# ---------------------------------------------------------------------------
# csp_git_branch
# Print the current git branch name, or nothing if not in a repo or git missing.
# Works without calling `git` by reading HEAD from .git (speed, no fork).
# ---------------------------------------------------------------------------
csp_git_branch() {
  local dir="$PWD"
  local gitdir
  while [ "$dir" != "/" ]; do
    if [ -d "$dir/.git" ]; then
      gitdir="$dir/.git"
      break
    elif [ -f "$dir/.git" ]; then
      # Submodule or worktree gitlink file.
      local linked
      linked=$(cat "$dir/.git" 2>/dev/null)
      case "$linked" in
        gitdir:*)
          gitdir="${linked#gitdir: }"
          ;;
        *)
          gitdir="$dir/.git"
          ;;
      esac
      break
    fi
    dir=$(dirname "$dir")
  done
  [ -z "${gitdir:-}" ] && return 0

  # Read HEAD ref.
  local head_file="$gitdir/HEAD"
  [ -f "$head_file" ] || return 0
  local head
  head=$(cat "$head_file" 2>/dev/null)
  case "$head" in
    ref:\ refs/heads/*)
      printf '%s' "${head#ref: refs/heads/}"
      ;;
    ref:\ refs/tags/*)
      printf 'tag:%s' "${head#ref: refs/tags/}"
      ;;
    *)
      # Detached HEAD. Print short SHA.
      printf '%s...' "${head%%$'\n'}"
      ;;
  esac
}

# ---------------------------------------------------------------------------
# csp_last_status
# Print exit code indicator dot (green ok / red fail).
# Relies on $? via a prompt hook; we pass it explicitly to csp_prompt.
# ---------------------------------------------------------------------------
csp_last_status() {
  local status=$1
  if [ "$status" -eq 0 ]; then
    printf '%s%s%s' "$CSP_COLOR_OK" 'o' "$CSP_COLOR_RESET"
  else
    printf '%sx%s' "$CSP_COLOR_FAIL" 'x' "$CSP_COLOR_RESET"
  fi
}

# ---------------------------------------------------------------------------
# csp_prompt STATUS
# Assemble the full prompt string (without trailing space and PS1 format codes).
# This prints to stdout.
# ---------------------------------------------------------------------------
csp_prompt() {
  local status=$1
  local out=""

  # Exit code indicator.
  out="$out$(csp_last_status "$status") "

  # Hostname (when CSP_SHOW_HOST=1 or running over SSH).
  if [ "${CSP_SHOW_HOST:-0}" = "1" ] || [ -n "${SSH_TTY:-}${SSH_CONNECTION:-}" ]; then
    out="$out${CSP_COLOR_HOST}$(hostname -s)${CSP_COLOR_RESET} "
  fi

  # Abbreviated path.
  out="$out${CSP_COLOR_DIR}$(csp_path_short "$PWD")${CSP_COLOR_RESET}"

  # Git branch.
  local branch
  branch=$(csp_git_branch)
  if [ -n "$branch" ]; then
    out="$out ${CSP_COLOR_GIT}(${branch})${CSP_COLOR_RESET}"
  fi

  # Prompt terminator.
  printf '%s $ ' "$out"
}

# ---------------------------------------------------------------------------
# csp_disable_color
# Turn off all colors (useful for dumb terminals or piping output).
# ---------------------------------------------------------------------------
csp_disable_color() {
  export CSP_COLOR_DIR=''
  export CSP_COLOR_HOST=''
  export CSP_COLOR_GIT=''
  export CSP_COLOR_OK=''
  export CSP_COLOR_FAIL=''
  export CSP_COLOR_RESET=''
}

# ---------------------------------------------------------------------------
# Installation: hook into PROMPT_COMMAND (bash) or precmd (zsh).
# We only do this automatically if the script is sourced (not executed).
# ---------------------------------------------------------------------------
csp_install() {
  if [ -n "${BASH_VERSION:-}" ]; then
    __csp_set_prompt() {
      # We capture $? via a trap; but PROMPT_COMMAND runs after the command,
      # and $? is still valid only if we read it FIRST.
      local rc=$?
      PS1="$(csp_prompt "$rc")"
    }
    PROMPT_COMMAND="__csp_set_prompt${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
  elif [ -n "${ZSH_VERSION:-}" ]; then
    __csp_set_prompt() {
      # In zsh, $? is preserved if this is the first statement.
      local rc=$?
      PS1="$(csp_prompt "$rc")"
    }
    # precmd_functions is a standard zsh hook array.
    if ! printf '%s\n' "${precmd_functions[@]}" 2>/dev/null | grep -qx '__csp_set_prompt'; then
      precmd_functions+=(__csp_set_prompt)
    fi
  else
    printf 'cross-shell-prompt: unsupported shell. Use bash or zsh.\n' >&2
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Source detection: auto-install the prompt hook when this file is sourced,
# and print a demo when executed directly as a standalone script.
# ---------------------------------------------------------------------------
csp_is_sourced() {
  # bash: BASH_SOURCE differs from $0 only when sourced.
  if [ -n "${BASH_VERSION:-}" ]; then
    [ "${BASH_SOURCE[0]:-$0}" != "$0" ]
    return $?
  fi
  # zsh: ZSH_EVAL_CONTEXT starts with "toplevel" when executed, "filesh" when sourced.
  if [ -n "${ZSH_VERSION:-}" ]; then
    [ "${ZSH_EVAL_CONTEXT:-toplevel}" = "filesh" ]
    return $?
  fi
  return 1
}

if csp_is_sourced; then
  csp_install
else
  # Executed directly. Print a colorless demo so it works in any terminal.
  csp_disable_color
  printf 'cross-shell-prompt demo\n\n'
  printf '  normal:  %s\n' "$(csp_prompt 0)"
  printf '  failed:  %s\n' "$(csp_prompt 127)"
  printf '\nInstall by adding this line to your ~/.bashrc or ~/.zshrc:\n'
  printf '  source %s/prompt.sh\n' "$(pwd)"
fi
