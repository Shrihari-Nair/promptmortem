"""Score versioned judge prompts (backend/app/agents/prompts/judge.<version>.md) against a project's labelled traces.
  cd backend && uv run python ../scripts/judge_version_eval.py METRIC_ID v1 v2
Calls the metric's real judge model (at most calibration_max_items calls per version; repeats are cached)."""
import asyncio
import json
import sys

from app.core.db import engine
from app.core.llm import LLMClient
from app.pipeline import metric_plan

if len(sys.argv) < 3:
    sys.exit(__doc__)
print(json.dumps(asyncio.run(metric_plan.compare_judge_versions(engine, LLMClient(engine), sys.argv[1], sys.argv[2:])), indent=1))
