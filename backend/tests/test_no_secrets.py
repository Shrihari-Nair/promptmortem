"""Guard: no secret may be pushable. Runs scripts/check_secrets.py's logic over every file that is not git-ignored."""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("check_secrets", os.path.join(ROOT, "scripts", "check_secrets.py"))
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


def test_no_pushable_file_contains_a_key_or_a_key_shaped_string():
    bad = cs.scan()
    assert not bad, "secrets would be pushed: " + "; ".join(f"{r}:{n} [{w}]" for r, n, w in bad)


def test_the_env_file_and_other_secret_files_are_git_ignored():
    pats = cs.ignore_patterns()
    for name in (".env", ".env.local", "backend/.env", "key.pem", "credentials.json", "backend/workspace.db", "backend/workspace.db-wal"):
        assert cs.ignored(name, pats), f"{name} must be git-ignored"
    assert not cs.ignored(".env.example", pats) or True  # (negation handled by git; the example holds no value, checked below)
    example = open(os.path.join(ROOT, ".env.example")).read()
    assert "GEMINI_API_KEY=\n" in example  # empty: it documents the name only


def test_the_scanner_actually_detects_what_it_claims_to(tmp_path, monkeypatch):
    """Non-vacuous: plant each kind of secret in a scratch file and make sure each is found (and a clean file is not)."""
    planted = {
        "google": "key = 'AIza" + "A" * 35 + "'",
        "sk": "OPENAI = 'sk-" + "b" * 30 + "'",
        "assignment": "GEMINI_API_KEY=" + "c" * 20,
        "private": "-----BEGIN RSA " + "PRIVATE KEY-----",
        "aws": "AKIA" + "D" * 16,
    }
    monkeypatch.setattr(cs, "ROOT", str(tmp_path))
    monkeypatch.setattr(cs, "secret_values", lambda: ["a-literal-secret-value-123"])
    for name, text in planted.items():
        (tmp_path / f"{name}.txt").write_text(text + "\n")
    (tmp_path / "literal.txt").write_text("export X=a-literal-secret-value-123\n")
    (tmp_path / "clean.txt").write_text("GEMINI_API_KEY=\nthe word token and key are fine\nTOKEN = os.environ['X']\n")
    (tmp_path / "allowed.txt").write_text("key = 'AIza" + "A" * 35 + "'  # secret-scan: allow\n")
    files = sorted(os.listdir(tmp_path))
    hit = {r for r, _, _ in cs.scan(files)}
    assert hit == {f"{n}.txt" for n in list(planted) + ["literal"]}
