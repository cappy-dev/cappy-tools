# tailscale-acl-audit

Checks Tailscale ACL files for common policy mistakes without sending the policy over the network.

## Why use it

Tailnet ACL files are flexible but easy to get subtly wrong. A copied and
pasted rule can accidentally create a duplicate. A wildcard `dst` can let
the wrong host in. A `reject` rule placed after an `accept` rule is silently
shadowed and never fires. This tool catches those issues before you push the
policy to Tailscale.

It is safe to run on a schedule. It is a single Python file with no third
party packages, no networking, and no side effects beyond reading the file
you pass in.

## Requirements
- Python 3.8 or newer
- No pip install required. The standard library is enough.

## Installation
Copy the script somewhere on your PATH, or just run it from the repo.

```bash
# Option A: clone and run in place
git clone https://github.com/cappy-dev/cappy-tools.git
cd cappy-tools/tools/tailscale-acl-audit
python3 tailscale_acl_audit.py path/to/your.acl.hujson

# Option B: drop the single file into your dotfiles
cp tailscale_acl_audit.py ~/.local/bin/tailscale_acl_audit
chmod +x ~/.local/bin/tailscale_acl_audit
tailscale_acl_audit path/to/your.acl.hujson
```

## Usage
```text
usage: tailscale_acl_audit [-h] [-q] [--exit-code] [--json] [-V] aclfile

positional arguments:
  aclfile       path to the .acl.hujson file to audit

optional arguments:
  -h, --help    show this help message and exit
  -q, --quiet   only print findings, suppress positive summary line
  --exit-code   exit nonzero if any FAIL finding is present (useful in CI)
  --json        emit findings as JSON instead of human readable text
  -V, --version show version and exit
```

## Examples
Audit a policy file and print a human readable report:

```bash
python3 tailscale_acl_audit.py tailnet.acl.hujson
```

Typical output:

```text
tailscale acl audit: tailnet.acl.hujson
findings: 2 ok, 2 warn, 1 fail

  [FAIL] TAG002: acl references undefined tag tag:prod
        an acl entry references tag:prod but that tag has no entry in
        tagOwners. the rule will never match.

  [FAIL] ACL004: duplicate acl entry
        found an exact duplicate acl entry: action=accept src=['group:eng']
        dst=['tag:web:*']. this is almost certainly a copy and paste error.

  [WARN] SSH001: ssh rule missing close duration
        an ssh rule has no 'close' session duration check. consider adding a
        'close' value so idle sessions are automatically terminated.

  [WARN] CFG001: autoAdmin is on
        autoAdmin is true. every user who joins the tailnet is made an admin
        automatically. recommended only for very small or trusted tailnets.
```

Use JSON output for scripting:

```bash
python3 tailscale_acl_audit.py tailnet.acl.hujson --json
```

Use it as a CI gate that fails the build on any hard fail:

```bash
python3 tailscale_acl_audit.py tailnet.acl.hujson --exit-code || exit 1
```

Quiet mode prints only findings and nothing else:

```bash
python3 tailscale_acl_audit.py tailnet.acl.hujson -q
```

## What it checks

The audit rules are conservative. Warnings are things you should review on
purpose. Fails are very likely to bite you.

- `ACL001`: non-standard `action` value (anything other than accept or reject).
- `ACL002`: a `reject` rule with no `reason` field explaining why.
- `ACL003`: an `accept` rule with a wildcard destination such as `*`.
- `ACL004`: exactly duplicated acl entry, usually a copy and paste error.
- `ACL005`: a `reject` rule is shadowed by an earlier `accept` rule whose `src`
  and `dst` patterns overlap. Tailscale evaluates rules top to bottom and stops
  at the first match, so the earlier accept silently shadows this reject.
- `ACL006`: an acl entry with an empty `dst` array that matches nothing.
- `SSH001`: an ssh rule missing a `close` session duration check.
- `GRANT001`: a grant with a wildcard destination. Grants bypass the normal
  acl match path, so a wildcard grant is a broad capability.
- `CFG001`: `autoAdmin` is on, which makes every joining user an admin.
- `TAG001`: a tag whose owner is a wildcard such as `*`. Anyone who joins could
  mint that tag and inherit its acl permissions.
- `TAG002`: an acl entry references a tag that has no entry in `tagOwners`. The
  rule will never match anything.
- `GRP001`: a group reference in an acl source that is missing the `group:`
  prefix.

If a rule crashes for any reason, it records an `INTERNAL` finding instead of
bringing down the whole audit.

## License

AGPL-3.0. See the [root license](../../LICENSE).
