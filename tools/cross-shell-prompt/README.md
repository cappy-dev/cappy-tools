# cross-shell-prompt

A clean, informative shell prompt for bash and zsh. Zero dependencies, just
shell builtins. Drop it in and get a prompt that shows your last exit code,
current git branch, and an abbreviated working directory.

## Features

- Last command exit code indicator: green `o` on success, red `x` on failure
- Current git branch, detected by reading `.git/HEAD` directly (no `git` fork)
- Abbreviated working path: `~/projects/myapp` becomes `~/p/myapp`
- Optional hostname that shows automatically over SSH
- Color output by default, plain mode for dumb terminals or logs
- Works in bash 3.2+ and zsh 5+

## What it looks like

```
o ~/p/myapp (main) $ xxx ~/p/myapp (main) $
```

The left prompt shows a normal successful command. The right shows what appears
after a failing command (exit code 1 or higher).

## Installation

Add one line to your shell config (`~/.bashrc` for bash, `~/.zshrc` for zsh):

```sh
source /path/to/prompt.sh
```

Then start a new shell or run `source ~/.bashrc` (or `~/.zshrc`).

## Install via git clone

```sh
git clone https://github.com/cappy-dev/cross-shell-prompt.git
echo "source $(pwd)/cross-shell-prompt/prompt.sh" >> ~/.bashrc
```

For zsh, use `~/.zshrc` instead.

## Quick demo (no install needed)

```sh
./prompt.sh
```

Prints a sample prompt so you can see the format before installing.

## Configuration

All settings are environment variables. Set them before sourcing the script.

### `CSP_SHOW_HOST`

Set to `1` to always show the hostname (even outside SSH):

```sh
export CSP_SHOW_HOST=1
source /path/to/prompt.sh
```

The hostname shows automatically when `SSH_CONNECTION` or `SSH_TTY` is set.

### Colors

Override any color before sourcing. Values are ANSI escape strings.

| Variable            | Default   | Used for            |
|---------------------|-----------|---------------------|
| `CSP_COLOR_DIR`     | bold cyan | Working directory   |
| `CSP_COLOR_HOST`    | magenta   | Hostname            |
| `CSP_COLOR_GIT`     | yellow    | Git branch          |
| `CSP_COLOR_OK`      | green     | Success indicator   |
| `CSP_COLOR_FAIL`    | red       | Failure indicator   |
| `CSP_COLOR_RESET`   | reset     | Color reset         |

Example with custom colors:

```sh
export CSP_COLOR_DIR=$'\033[1;34m'
source /path/to/prompt.sh
```

## Disable colors

Call `csp_disable_color` after sourcing:

```sh
source /path/to/prompt.sh
csp_disable_color
```

Useful for dumb terminals or when piping shell output to a log.

## How the path abbreviation works

The prompt keeps the first letter of each intermediate directory but shows
the final directory in full. Your home directory is replaced with `~`.

| Full path                       | Prompt path        |
|---------------------------------|--------------------|
| `/home/user`                    | `~`                |
| `/home/user/projects/myapp`     | `~/p/myapp`        |
| `/etc/systemd/system`           | `/e/s/system`      |
| `/var/log/nginx`                | `/v/l/nginx`       |
| `/`                             | `/`                |

This keeps long paths short without hiding the last directory, which is the
one you usually care about.

## How git branch detection works (no `git` command needed)

The script walks up the directory tree until it finds a `.git` directory or
file. It then reads `.git/HEAD` directly to determine the branch name. This
avoids forking `git` on every prompt, which makes the prompt fast even in
large repos.

Detached HEAD state shows the short commit hash followed by `...`.
Tags show `tag:NAME` format.

## Compatibility

Tested with:

- bash 3.2 (macOS default), 4.x, 5.x
- zsh 5.x

Not compatible with fish, tcsh, or other shells. The script uses `PROMPT_COMMAND`
(bash) or `precmd_functions` (zsh) hooks that only exist in those two shells.

## Files

- `prompt.sh` - the full script. Source this file from your shell config.
- `LICENSE` - MIT license.

## License

MIT. See `LICENSE`.
