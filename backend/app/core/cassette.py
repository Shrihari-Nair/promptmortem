"""Record/replay of model calls. The LLMCall table is already a content-keyed cache, so a cassette is just a JSON dump of
its rows. `load` seeds the cache; `offline` is a provider that fails loudly on any call the cassette does not cover.

  record: python -m app.core.cassette dump tests/cassettes/billing.json [model-prefix]   (after a real smoke run)
  replay: load(engine, path); LLMClient(engine, provider=offline, fake=offline)

ponytail: replay only covers calls whose messages are stable between runs (scenario ids are random, so a full pipeline
replay will miss); fine for agent-level and judge-level replays."""
import json
import sys
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.core.models import LLMCall

FIELDS = ("cache_key", "model", "response", "tokens_in", "tokens_out", "cost", "latency")


class CassetteMiss(Exception):
    pass


def dump(engine: Engine, path: str | Path, model_prefix: str = "") -> int:
    with Session(engine) as s:
        rows = [{f: getattr(r, f) for f in FIELDS} for r in s.exec(select(LLMCall)).all() if r.model.startswith(model_prefix)]
    Path(path).write_text(json.dumps({"calls": rows}, indent=1, sort_keys=True))
    return len(rows)


def load(engine: Engine, path: str | Path) -> int:
    calls = json.loads(Path(path).read_text())["calls"]
    with Session(engine) as s:
        have = set(s.exec(select(LLMCall.cache_key)).all())
        new = [LLMCall(**c) for c in calls if c["cache_key"] not in have]
        s.add_all(new)
        s.commit()
    return len(new)


async def offline(model: str, messages: list[dict], params: dict):
    raise CassetteMiss(f"{model}: call not in the cassette (prompt changed? re-record): {str(messages[-1]['content'])[:120]!r}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "dump":
        sys.exit(__doc__)
    from app.core.db import engine
    print(dump(engine, sys.argv[2], *sys.argv[3:4]), "calls written")
