#!/usr/bin/env python3
"""Fail if a secret could be pushed. Scans every file that is not git-ignored for:
  - the literal VALUES of any secret in .env / the environment (so a key pasted anywhere is caught, whatever its shape),
  - key-shaped strings (Google AIza..., sk-..., AWS AKIA..., private-key blocks),
  - a non-empty KEY=value assignment for *_API_KEY / *_TOKEN / *_SECRET names.
Prints file:line and the pattern NAME only, never the secret. Mark a deliberate fake with `secret-scan: allow` on its line.
Usage: python scripts/check_secrets.py   (exit 1 on a finding). Used by the test suite, CI and the git pre-commit hook."""
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP_DIRS = {"node_modules", ".venv", ".git", "dist", "__pycache__", ".pytest_cache", ".vite", "test-results", "playwright-report", ".mypy_cache"}
PATTERNS = {
    "google-api-key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "sk-style-key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key-block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret-assignment": re.compile(r"(?i)\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*[\"']?(?!\s|[\"']|\$|<|your|xxx|changeme|\.\.\.|none|null|true|false|\{|\[|\()[A-Za-z0-9_\-./+]{12,}"),
}
SECRET_ENV_NAMES = re.compile(r"(?i)(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS)")


def ignore_patterns() -> list[str]:
    try:
        return [l.strip() for l in open(os.path.join(ROOT, ".gitignore")) if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []


def ignored(rel: str, pats: list[str]) -> bool:
    parts = rel.split("/")
    for p in pats:
        if p.startswith("!"):
            continue
        p = p.rstrip("/")
        if any(fnmatch.fnmatch(x, p) for x in parts) or fnmatch.fnmatch(rel, p):
            return True
    return False


def tracked_candidates() -> list[str]:
    """Files that could be pushed: `git ls-files` in a repo, else everything not matched by .gitignore."""
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [l for l in out.splitlines() if l]
    except Exception:
        pats, files = ignore_patterns(), []
        for d, dirs, names in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for n in names:
                rel = os.path.relpath(os.path.join(d, n), ROOT).replace(os.sep, "/")
                if not ignored(rel, pats):
                    files.append(rel)
        return files


def secret_values() -> list[str]:
    """Literal secret values from .env files and the environment (len >= 8)."""
    vals = set()
    for f in (os.path.join(ROOT, ".env"), os.path.expanduser("~/.env")):
        try:
            for l in open(f):
                if "=" in l and not l.lstrip().startswith("#"):
                    k, v = l.split("=", 1)
                    v = v.strip().strip("\"'")
                    if SECRET_ENV_NAMES.search(k) and len(v) >= 8:
                        vals.add(v)
        except OSError:
            pass
    for k, v in os.environ.items():
        if SECRET_ENV_NAMES.search(k) and len(v) >= 8:
            vals.add(v)
    return sorted(vals, key=len, reverse=True)


def scan(files: list[str] | None = None) -> list[tuple[str, int, str]]:
    values, findings = secret_values(), []
    for rel in files if files is not None else tracked_candidates():
        path = os.path.join(ROOT, rel)
        try:
            if os.path.getsize(path) > 8_000_000:
                continue
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if "secret-scan: allow" in line:
                continue
            for name, rx in PATTERNS.items():
                if rx.search(line):
                    findings.append((rel, n, name))
            if any(v in line for v in values):
                findings.append((rel, n, "literal value of a secret from .env / the environment"))
    return findings


if __name__ == "__main__":
    files = tracked_candidates()
    bad = scan(files)
    for rel, n, what in bad:
        print(f"SECRET? {rel}:{n}  [{what}]")
    print(f"scanned {len(files)} pushable files: {'FOUND ' + str(len(bad)) + ' problem(s)' if bad else 'clean'}")
    sys.exit(1 if bad else 0)
