"""Declarative programmatic checks (no code execution) and metric applicability."""
import json
import re

import jsonschema
from langdetect import DetectorFactory, detect

from app.agents.schemas import Check, ProgDef

DetectorFactory.seed = 0  # deterministic language detection


def run_check(c: Check, output: str, expected: str = "") -> tuple[bool, str]:
    t, o = c.type, output
    ic = re.IGNORECASE if c.ignore_case else 0
    if t == "max_words":
        n = len(o.split())
        return n <= c.value, f"{n} words (max {c.value})"
    if t == "min_words":
        n = len(o.split())
        return n >= c.value, f"{n} words (min {c.value})"
    if t == "max_chars":
        return len(o) <= c.value, f"{len(o)} chars (max {c.value})"
    if t == "regex_required":
        return bool(re.search(c.pattern, o, ic)), c.message or f"required /{c.pattern}/ {'found' if re.search(c.pattern, o, ic) else 'missing'}"
    if t == "forbidden_regex":
        m = re.search(c.pattern, o, ic)
        return not m, c.message or (f"forbidden /{c.pattern}/ matched {m.group(0)!r}" if m else "no forbidden match")
    if t in ("contains_all", "contains_none"):
        hay = o.lower() if c.ignore_case else o
        found = [v for v in c.values if (v.lower() if c.ignore_case else v) in hay]
        if t == "contains_all":
            missing = [v for v in c.values if v not in found]
            return not missing, f"missing {missing}" if missing else "all present"
        return not found, f"contains forbidden {found}" if found else "none present"
    if t == "json_valid":
        try:
            json.loads(o.strip())
            return True, "valid JSON"
        except ValueError as e:
            return False, f"invalid JSON: {e}"
    if t == "json_schema":
        try:
            jsonschema.validate(json.loads(o.strip()), c.schema_)
            return True, "matches schema"
        except (ValueError, jsonschema.ValidationError) as e:
            return False, f"schema check failed: {str(e).splitlines()[0]}"
    if t == "equals":
        want = expected if c.value == "$expected" else c.value
        a, b = o.strip(), str(want).strip()
        ok = a.lower() == b.lower() if c.ignore_case else a == b
        return ok, "equals expected" if ok else "differs from expected"
    if t == "starts_with":
        ok = o.lstrip().lower().startswith(c.value.lower()) if c.ignore_case else o.lstrip().startswith(c.value)
        return ok, f"{'starts' if ok else 'does not start'} with {c.value!r}"
    if t == "language_is":
        try:
            lang = detect(o)
        except Exception:  # too short / no letters
            return False, "language undetectable"
        return lang == c.value.lower(), f"detected {lang} (want {c.value})"
    raise ValueError(t)


def run_programmatic(defn: dict, output: str, expected: str = "") -> tuple[float, str]:
    """-> (value in 0..1, rationale listing the failing checks)."""
    d = ProgDef.model_validate(defn)
    res = [run_check(c, output, expected) for c in d.checks]
    oks = [ok for ok, _ in res]
    value = {"all_pass": float(all(oks)), "any_pass": float(any(oks)), "mean": sum(oks) / len(oks)}[d.aggregate]
    failed = [f"{c.type}: {msg}" for c, (ok, msg) in zip(d.checks, res) if not ok]
    return value, "; ".join(failed) if failed else "all checks passed"


def metric_applies(applies_to: dict | None, category: str, tags: list[str]) -> bool:
    a = applies_to or {}
    cats, want_tags = a.get("categories"), a.get("tags")
    cat_ok = not cats or "*" in cats or category in cats
    tag_ok = not want_tags or "*" in want_tags or bool(set(want_tags) & set(tags))
    return cat_ok and tag_ok
