#!/usr/bin/env python3
"""
tailscale_acl_audit.py - Zero-dependency linter for Tailscale ACL files.

Tailscale stores access control policy in tailnet ACL files written in huJSON
(human JSON). This script parses and audits a tailnet ACL file for common
configuration mistakes and security gotchas, then prints a human readable
report. It never contacts any network endpoint and never sends your policy
file anywhere.

Author: cappy-dev
License: AGPL-3.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

OK = 0
WARNING = 1
FAIL = 2

EMOJI = {OK: "OK", WARNING: "WARN", FAIL: "FAIL"}


# ---------------------------------------------------------------------------
# Tiny huJSON parser
# ---------------------------------------------------------------------------
#
# huJSON is JSON plus comments, trailing commas, and unquoted object keys. We
# strip all of that out into plain JSON before handing it to json.loads. This
# is intentionally simple and does not attempt to be a perfect JSONC parser.
# It is good enough for the real-world tailnet ACL files that Tailscale emits.


_comment_re = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_trailing_comma_re = re.compile(r",(\s*[}\]])")


def _strip_hujon(source: str) -> str:
    """Remove // comments and trailing commas from huJSON text."""
    no_comments = _comment_re.sub("", source)
    # Loop replacement because trailing commas can appear nested.
    prev = None
    cleaned = no_comments
    while prev != cleaned:
        prev = cleaned
        cleaned = _trailing_comma_re.sub(r"\1", cleaned)
    return cleaned


def _quoting_keys_re(source: str) -> str:
    """Quote bare object keys like {autoAdmin: true} -> {"autoAdmin": true}."""
    # Match an identifier preceded by { or , and optional whitespace, followed by ':'
    bare_key_re = re.compile(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)')
    prev = None
    cleaned = source
    while prev != cleaned:
        prev = cleaned
        cleaned = bare_key_re.sub(r'\1"\2"\3', cleaned)
    return cleaned


def parse_acl_file(path: Path) -> dict[str, Any]:
    """Read and parse a huJSON tailnet ACL file into a Python dict."""
    raw = path.read_text(encoding="utf-8")
    normalized = _quoting_keys_re(_strip_hujon(raw))
    try:
        return json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"error: could not parse {path} as huJSON: {exc}\n"
            f"       the file may be malformed or use a tailnet policy format "
            f"that this tool does not yet understand."
        )


# ---------------------------------------------------------------------------
# Audit finding types
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    code: str
    title: str
    severity: int
    detail: str = ""
    line: int | None = None


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    @property
    def passed(self) -> bool:
        return not any(f.severity == FAIL for f in self.findings)

    def count_str(self) -> str:
        ok = sum(1 for f in self.findings if f.severity == OK)
        warn = sum(1 for f in self.findings if f.severity == WARNING)
        fail = sum(1 for f in self.findings if f.severity == FAIL)
        return f"{ok} ok, {warn} warn, {fail} fail"


# ---------------------------------------------------------------------------
# Heuristics / audit rules
# ---------------------------------------------------------------------------

# These rules are inspired by common pitfalls in real-world tailnet ACL files.
# They are conservative: warnings are things you should review, fails are very
# likely to bite you.


def _is_wildcard(pattern: str) -> bool:
    return pattern in {"*", "autogroup:everyone", "autogroup:internet"} or (
        "*" in pattern and ":" not in pattern
    )


def _collect_acl_entries(policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of {action, src, dst} dicts from either 'acls' or deprecated 'acl'."""
    entries_raw = policy.get("acls", policy.get("acl", []))
    flat = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        flat.append(entry)
    return flat


def check_acl_entry_nonstandard_actions(policy: dict[str, Any], report: Report) -> None:
    entries = _collect_acl_entries(policy)
    for entry in entries:
        action = entry.get("action", "accept")
        if action not in ("accept", "reject"):
            report.add(Finding(
                code="ACL001",
                title="non-standard action",
                severity=WARNING,
                detail=f'acl entry uses action "{action}"; tailscale only '
                       f'recognizes "accept" and "reject".',
            ))


def check_reject_without_reason(policy: dict[str, Any], report: Report) -> None:
    entries = _collect_acl_entries(policy)
    for entry in entries:
        action = entry.get("action", "accept")
        if action == "reject" and "reason" not in entry:
            report.add(Finding(
                code="ACL002",
                title="reject without reason",
                severity=WARNING,
                detail="a reject rule has no 'reason' field. specifying a reason "
                       "helps operators understand why a flow was blocked.",
            ))


def check_wildcard_accept(policy: dict[str, Any], report: Report) -> None:
    entries = _collect_acl_entries(policy)
    for entry in entries:
        action = entry.get("action", "accept")
        dst = entry.get("dst", [])
        if action == "accept" and isinstance(dst, list) and any(_is_wildcard(d) for d in dst):
            report.add(Finding(
                code="ACL003",
                title="accept rule targets wildcard destination",
                severity=WARNING,
                detail="an accept rule has a wildcard destination. this is "
                       "fine for autogroups you control, but double check it.",
            ))


def check_ssh_without_session_duration(policy: dict[str, Any], report: Report) -> None:
    ssh = policy.get("ssh", [])
    if not isinstance(ssh, list):
        return
    for rule in ssh:
        if not isinstance(rule, dict):
            continue
        checks = rule.get("checks") or []
        if not any("close" in c for c in checks):
            report.add(Finding(
                code="SSH001",
                title="ssh rule missing close duration",
                severity=WARNING,
                detail="an ssh rule has no 'close' session duration check. "
                       "consider adding a 'close' value so idle sessions are "
                       "automatically terminated.",
            ))


def check_grants_too_broad(policy: dict[str, Any], report: Report) -> None:
    grants = policy.get("grants", [])
    if not isinstance(grants, list):
        return
    for grant in grants:
        if not isinstance(grant, dict):
            continue
        dests = grant.get("dst", [])
        if isinstance(dests, list) and any(_is_wildcard(d) for d in dests):
            report.add(Finding(
                code="GRANT001",
                title="grant targets wildcard destination",
                severity=WARNING,
                detail="a grant has a wildcard destination. grants bypass the "
                       "normal ACL match path, so a wildcard grant can let a "
                       "principal read or control anything. audit carefully.",
            ))


def check_auto_admin_true(policy: dict[str, Any], report: Report) -> None:
    if policy.get("autoAdmin") is True:
        report.add(Finding(
            code="CFG001",
            title="autoAdmin is on",
            severity=WARNING,
            detail="autoAdmin is true. every user who joins the tailnet is made "
                   "an admin automatically. recommended only for very small or "
                   "trusted tailnets.",
        ))


def check_tag_owners_wildcard(policy: dict[str, Any], report: Report) -> None:
    tag_owners = policy.get("tagOwners", {}) or {}
    for tag, owners in tag_owners.items():
        if isinstance(owners, list) and any(o in ("*", "autogroup:everyone") for o in owners):
            report.add(Finding(
                code="TAG001",
                title=f"tag {tag} is owner-wildcarded",
                severity=WARNING,
                detail=f"tag {tag} can be claimed by anyone (owner is a wildcard). "
                       f"a malicious joiner could mint that tag and inherit the "
                       f"ACL permissions attached to it.",
            ))


def check_groups_overlap_with_users(policy: dict[str, Any], report: Report) -> None:
    """Warn when an acl src references a bare group name instead of group:name.

    In tailscale ACL files the 'groups' object uses keys like 'group:eng'. When
    you reference a group in an acl 'src' you must use that same prefixed form.
    If you accidentally write the bare portion (e.g. 'eng' instead of
    'group:eng') tailscale will not match it against any user, so we warn.
    """
    groups = policy.get("groups", {}) or {}
    acls = _collect_acl_entries(policy)
    # Collect all names that are valid group references in this policy.
    valid_group_refs = set(groups.keys())
    # Also accept the convention that a group key may be stored without the
    # 'group:' prefix and the user is expected to reference it WITH the prefix.
    bare_keys = {k[len("group:"):] if k.startswith("group:") else k for k in valid_group_refs}
    for entry in acls:
        src = entry.get("src", [])
        if not isinstance(src, list):
            continue
        for s in src:
            # Skip anything that is not a plain identifier (no @, no :, no *).
            if "@" in s or ":" in s or "*" in s:
                continue
            if s in bare_keys and f"group:{s}" not in valid_group_refs and s not in valid_group_refs:
                report.add(Finding(
                    code="GRP001",
                    title=f"acl source '{s}' looks like a bare group name",
                    severity=WARNING,
                    detail=f"source pattern '{s}' matches a group defined in "
                           f"'groups', but it is missing the 'group:' prefix. "
                           f"tailscale will not resolve it. reference it as "
                           f"'group:{s}' instead.",
                ))


def check_duplicate_acl_entries(policy: dict[str, Any], report: Report) -> None:
    entries = _collect_acl_entries(policy)
    seen: set[tuple] = set()
    for entry in entries:
        key = (
            entry.get("action", "accept"),
            tuple(entry.get("src", [])),
            tuple(entry.get("dst", [])),
        )
        if key in seen:
            report.add(Finding(
                code="ACL004",
                title="duplicate acl entry",
                severity=FAIL,
                detail=f"found an exact duplicate acl entry: action={key[0]} "
                       f"src={list(key[1])} dst={list(key[2])}. this is almost "
                       f"certainly a copy and paste error.",
            ))
        else:
            seen.add(key)


def _patterns_may_overlap(a: str, b: str) -> bool:
    """A conservative test of whether two tailscale src/dst patterns could both
    match a single principal or flow.

    This intentionally keeps things loose: if we are not sure two patterns
    overlap, we err on the side of saying they might, so that the audit only
    *misses* shadows in well understood corner cases."""
    if a == b:
        return True
    if a == "*" or b == "*":
        return True
    # tag:web:22 vs tag:db:5432: never overlap on the same flow.
    if a.startswith("tag:") and b.startswith("tag:"):
        a_tag = a.rsplit(":", 1)[0] if a.count(":") >= 2 else a
        b_tag = b.rsplit(":", 1)[0] if b.count(":") >= 2 else b
        if a_tag != b_tag:
            return False
    # group:eng vs group:sales: never overlap.
    if a.startswith("group:") and b.startswith("group:"):
        if a != b:
            return False
    return True


def check_reject_before_accept(policy: dict[str, Any], report: Report) -> None:
    """Warn if a reject rule is shadowed by an earlier accept rule.

    Tailscale evaluates rules top to bottom and stops at the first match, so an
    earlier accept can override a later reject. We only flag when both the src
    and dst patterns could plausibly refer to the same flow, otherwise we would
    flag every reject-after-accept pair regardless of whether they overlap.
    """
    entries = _collect_acl_entries(policy)
    accepts: list[tuple[list, list]] = []
    for entry in entries:
        action = entry.get("action", "accept")
        src = entry.get("src", []) or []
        dst = entry.get("dst", []) or []
        if action == "accept":
            accepts.append((src, dst))
        elif action == "reject":
            already_reported_for = set()
            for acc_src, acc_dst in accepts:
                # An accept shadows this reject if both src and dst could overlap.
                src_overlaps = any(
                    _patterns_may_overlap(rs, rps)
                    for rs in src for rps in acc_src
                )
                dst_overlaps = any(
                    _patterns_may_overlap(rd, rpd)
                    for rd in dst for rpd in acc_dst
                )
                if not src_overlaps or not dst_overlaps:
                    continue
                key = (tuple(src), tuple(dst))
                if key in already_reported_for:
                    continue
                already_reported_for.add(key)
                report.add(Finding(
                    code="ACL005",
                    title="reject rule shadowed by an earlier accept rule",
                    severity=WARNING,
                    detail="a reject rule appears after an earlier accept rule "
                           "with overlapping src/dst patterns. tailscale evaluates "
                           "rules top to bottom and stops at the first match, so "
                           "the earlier accept can shadow this reject and let the "
                           "traffic through.",
                ))
                break  # one shadow warning per reject rule is enough


def check_empty_dst(policy: dict[str, Any], report: Report) -> None:
    entries = _collect_acl_entries(policy)
    for entry in entries:
        dst = entry.get("dst", [])
        if isinstance(dst, list) and not dst:
            report.add(Finding(
                code="ACL006",
                title="acl entry with empty dst",
                severity=FAIL,
                detail="an acl entry has an empty 'dst' array. this rule matches "
                       "nothing and is almost certainly a typo or accidental edit.",
            ))


def check_test_groups_for_unknown_tag(policy: dict[str, Any], report: Report) -> None:
    tag_owners = set((policy.get("tagOwners", {}) or {}).keys())
    acls = _collect_acl_entries(policy)
    for entry in acls:
        for field_name in ("src", "dst"):
            values = entry.get(field_name, [])
            if not isinstance(values, list):
                continue
            for s in values:
                if not isinstance(s, str) or not s.startswith("tag:"):
                    continue
                # Strip a trailing ':port' so tag:web:22 -> tag:web.
                tag_name = s.rsplit(":", 1)[0] if s.count(":") >= 2 else s
                if tag_name not in tag_owners:
                    report.add(Finding(
                        code="TAG002",
                        title=f"acl references undefined tag {tag_name}",
                        severity=FAIL,
                        detail=f"an acl entry references {tag_name} but that tag has "
                               f"no entry in tagOwners. the rule will never match.",
                    ))


# ---------------------------------------------------------------------------
# Auditing driver
# ---------------------------------------------------------------------------


AUDIT_FUNCS = [
    check_acl_entry_nonstandard_actions,
    check_reject_without_reason,
    check_wildcard_accept,
    check_ssh_without_session_duration,
    check_grants_too_broad,
    check_auto_admin_true,
    check_tag_owners_wildcard,
    check_groups_overlap_with_users,
    check_duplicate_acl_entries,
    check_reject_before_accept,
    check_empty_dst,
    check_test_groups_for_unknown_tag,
]


def run_audit(policy: dict[str, Any]) -> Report:
    report = Report()
    for func in AUDIT_FUNCS:
        try:
            func(policy, report)
        except Exception as exc:  # never let one rule take the whole audit down
            report.add(Finding(
                code="INTERNAL",
                title=f"audit rule {func.__name__} crashed",
                severity=FAIL,
                detail=str(exc),
            ))
    return report


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------


def format_report(report: Report, *, path: Path) -> str:
    lines: list[str] = []
    lines.append(f"tailscale acl audit: {path}")
    lines.append(f"findings: {report.count_str()}")
    lines.append("")
    if not report.findings:
        lines.append("  no findings. your tailnet policy looks clean.")
        return "\n".join(lines)
    for finding in report.findings:
        severity_label = EMOJI.get(finding.severity, "OK")
        title = finding.title
        code = finding.code
        lines.append(f"  [{severity_label}] {code}: {title}")
        if finding.detail:
            # Wrap detail to 74 columns so it lines up under the severity.
            wrapped = _wrap(finding.detail, width=74)
            for chunk in wrapped:
                lines.append(f"        {chunk}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _wrap(text: str, *, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    out: list[str] = []
    cur = ""
    for word in words:
        candidate = word if not cur else f"{cur} {word}"
        if len(candidate) <= width:
            cur = candidate
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tailscale_acl_audit",
        description=(
            "Audit a Tailscale tailnet ACL (huJSON) file for common "
            "configuration mistakes and security gotchas. Zero network "
            "calls, zero dependencies."
        ),
    )
    parser.add_argument(
        "aclfile",
        type=Path,
        help="path to the .acl.hujson file to audit",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="only print findings, suppress positive summary line",
    )
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit nonzero if any FAIL finding is present (useful in CI)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit findings as JSON instead of human readable text",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"tailscale_acl_audit {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.aclfile.exists():
        print(f"error: acl file not found: {args.aclfile}", file=sys.stderr)
        return 2
    policy = parse_acl_file(args.aclfile)
    report = run_audit(policy)
    if args.json:
        data = {
            "file": str(args.aclfile),
            "summary": report.count_str(),
            "findings": [
                {
                    "code": f.code,
                    "title": f.title,
                    "severity": EMOJI.get(f.severity, "OK"),
                    "detail": f.detail,
                }
                for f in report.findings
            ],
        }
        print(json.dumps(data, indent=2))
    else:
        print(format_report(report, path=args.aclfile))
    if args.exit_code:
        return 1 if not report.passed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
