# github-profile-info

A Python CLI that writes a Markdown summary of a public GitHub user or organization. It uses the public GitHub REST API and does not require authentication.

## Install

```sh
mkdir -p ~/.local/bin
curl -fsSL https://raw.githubusercontent.com/cappy-dev/cappy-tools/main/tools/github-profile-info/github-profile-info -o ~/.local/bin/github-profile-info
chmod +x ~/.local/bin/github-profile-info
```

Requires Python 3.8 or newer. The script uses only the Python standard library.

## Usage

```sh
# Profile, statistics, languages, and pinned repositories
github-profile-info torvalds

# Include the top 20 repositories by stars
github-profile-info torvalds --repos

# Skip the profile-page scrape for pinned repositories
github-profile-info torvalds --no-pinned

# Choose an output path
github-profile-info torvalds --output ~/docs/linus.md
```

The command name, arguments, default output file (`<username>-profile-info.md`), and Markdown output format match the standalone tool.

## Data and limits

The tool fetches public profile data and repositories from the GitHub REST API. Pinned repositories come from the public profile page and may stop working if GitHub changes its HTML. Unauthenticated API access is limited to 60 requests per hour per IP. Repository pagination can use several requests for accounts with many repositories.

## Tests

Run the offline tests from the repository root:

```sh
python3 -m unittest discover -s tools/github-profile-info/tests -v
```

Tests replace network responses with fixtures. They do not contact GitHub.

## License and provenance

This tool is covered by the repository's [AGPL-3.0 license](../../LICENSE). The migration manifest at the repository root records the standalone source commit, tree, and script hash.
