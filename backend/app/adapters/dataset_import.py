"""CSV/JSONL import for datasets and traces (generic JSONL: input, optional output, optional expected)."""
import csv
import io
import json


def parse_records(content: str, fmt: str) -> list[dict]:
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(content)))
    if fmt == "jsonl":
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    raise ValueError(f"unknown format {fmt!r} (use csv or jsonl)")


def to_items(records: list[dict], mapping: dict | None = None, input_var: str = "user_message") -> list[dict]:
    """mapping = {"input": {var: column}, "expected": column|None, "output": column|None}.
    Without a mapping each record needs an `input` field (object, or string -> {input_var: string})."""
    mapping = mapping or {}
    items = []
    for i, rec in enumerate(records):
        if "input" in mapping:
            inp = {var: rec.get(col) for var, col in mapping["input"].items()}
        else:
            raw = rec.get("input")
            if raw is None:
                raise ValueError(f"record {i} has no 'input' field (columns: {sorted(rec)}); pass a column mapping")
            inp = raw if isinstance(raw, dict) else {input_var: raw}
        if not any(str(v or "").strip() for v in inp.values()):
            raise ValueError(f"record {i} has an empty input")
        exp_col, out_col = mapping.get("expected", "expected"), mapping.get("output", "output")
        items.append({"input": inp, "expected": rec.get(exp_col) or None, "output": rec.get(out_col) or None})
    return items
