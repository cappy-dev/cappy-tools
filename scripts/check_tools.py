#!/usr/bin/env python3
"""Validate tool documentation, license placement, and script syntax."""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    tools = sorted(path for path in (ROOT / "tools").iterdir() if path.is_dir())
    root_readme = (ROOT / "README.md").read_text()
    listed_tools = set(re.findall(r"\]\(tools/([^/)]+)\)", root_readme))
    tool_names = {tool.name for tool in tools}
    for missing in sorted(tool_names - listed_tools):
        errors.append(f"README.md: missing tools entry for {missing}")
    for stale in sorted(listed_tools - tool_names):
        errors.append(f"README.md: tools entry points to missing directory {stale}")
    licenses = [path for path in ROOT.rglob("LICENSE*") if ".git" not in path.parts]
    if licenses != [ROOT / "LICENSE"]:
        errors.append("Keep the license only at the repository root.")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in (ROOT / "LICENSE").read_text():
        errors.append("Root LICENSE must contain the full AGPL text.")
    python_files = 0
    shell_files = 0
    for tool in tools:
        readme = tool / "README.md"
        if not readme.exists():
            errors.append(f"{tool.name}: missing README.md")
            continue
        if "../../LICENSE" not in readme.read_text():
            errors.append(f"{tool.name}: README must link to the root license")
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix == ".py":
            python_files += 1
            try:
                compile(path.read_text(), str(path), "exec")
            except SyntaxError as error:
                errors.append(f"{path.relative_to(ROOT)}: {error}")
        elif path.suffix == ".sh":
            shell_files += 1
            check = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            if check.returncode:
                errors.append(f"{path.relative_to(ROOT)}: {check.stderr.strip()}")
        elif path.suffix == ".md":
            text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
            text = re.sub(r"`[^`]*`", "", text)
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                target = urlsplit(link)
                if target.scheme or target.netloc or not target.path:
                    continue
                if not (path.parent / unquote(target.path)).exists():
                    errors.append(f"{path.relative_to(ROOT)}: missing {link}")
    for error in errors:
        print(error)
    print(f"Checked {len(tools)} tool directories, {python_files} Python files, and {shell_files} shell files; {len(errors)} errors.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
