"""Deterministic mock provider (model names "fake/*"). Output is a pure function of (messages, seed).
Agent prompts start with `<!-- agent: NAME vN -->`; the fake returns canned, schema-valid JSON for each agent.
Scenario text depends on the whole prompt, so reviewer feedback changes what is generated.
M6 will extend this with noise injection and planted regressions."""
import hashlib
import json
import random
import re

FAKE_COST_PER_TOKEN = 1e-6
_WORDS = ("invoice refund plan upgrade downgrade charge twice account password login billing cancel receipt "
          "tax address card expired failed pending update export report seats team admin trial renewal discount "
          "coupon policy support urgent angry confused thanks hello help quickly yesterday monthly annual "
          "payment method statement balance overdue credit dispute fee proration contract quote vat currency "
          "email phone chat agent escalate manager complaint delay slow broken missing wrong duplicate access "
          "limit quota storage api key token webhook integration sync import data delete restore backup").split()


def quality(text: str) -> float:
    """Planted notion of a good output: no refund over-promise, within 120 words."""
    return -1.0 * ("full refund" in text.lower()) - 0.5 * (len(text.split()) > 120)


_APP = re.compile(r"<!-- fake-app: (\{.*?\}) -->")


def _app_reply(prompt: str, seed) -> str | None:
    """Fake *application*: behaviour planted by a `<!-- fake-app: {...} -->` directive in the prompt text.
    verbose_on_explain: replies to 'explain' questions run ~160 words. overpromise: chance a refund question gets
    a policy-violating promise. noise: chance that decision flips at random (old-vs-old variance).
    Randomness is seeded by prompt text + seed (= repeat index), so old and new are independent and repeats differ."""
    m = _APP.search(prompt)
    if not m:
        return None
    params, body = json.loads(m.group(1)), _APP.sub("", prompt).lower()
    r = random.Random(hashlib.sha256(f"{prompt}|{seed}".encode()).hexdigest())
    n = (160 if params.get("verbose_on_explain") and "explain" in body else 60) + r.randint(-5, 5)
    text = "Thanks for reaching out. " + " ".join(r.choice(_WORDS) for _ in range(n - 4))
    if "refund" in body:
        promise = r.random() < params.get("overpromise", 0.0)
        if r.random() < params.get("noise", 0.0):
            promise = not promise
        text += " You will definitely get a full refund within 2 days." if promise else " I can check whether this is eligible for a refund."
    return text


def _agent_reply(agent: str, prompt: str, rng: random.Random) -> dict | None:
    if agent == "context_analyzer":
        return {"task": "Answer customer questions", "user_types": ["customer"], "input_types": ["free text"],
                "output_format": "short plain text", "risk_areas": ["over-promising refunds", "privacy"],
                "success_criteria": ["accurate", "concise"], "assumptions": ["English-speaking users"]}
    if agent == "diff_analyzer":
        return {"changes": [
            {"id": "chg_1", "type": "added_constraint", "old_text": None, "new_text": "Answer in under 120 words.",
             "intent": "reduce verbosity", "could_affect": ["length of answers", "multi-part answers"], "risk": "medium"},
            {"id": "chg_2", "type": "tone_change", "old_text": "helpful", "new_text": "concise",
             "intent": "terser tone", "could_affect": ["empathy"], "risk": "low"}]}
    if agent == "scenario_generator":
        n = int(re.search(r"^N=(\d+)$", prompt, re.M).group(1))
        cat = re.search(r"^CATEGORY=(\w+)$", prompt, re.M).group(1)
        chg = re.search(r"^CHANGE_ID=(\w+)$", prompt, re.M).group(1)
        out = []
        for _ in range(n):
            words = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(6, 40)))  # varied lengths => varied cost
            out.append({"input": {"user_message": f"{words}"}, "rationale": f"{cat} case",
                        "expected_behavior": f"Handles the {cat} request correctly.",
                        "tags": [cat, rng.choice(["billing", "account", "refund", "plan"])],
                        "linked_changes": [] if chg == "none" else [chg],
                        "severity": rng.randint(1, 5), "diff_relevance": round(rng.random(), 2)})
        return {"scenarios": out}
    if agent == "scenario_critic":
        rnd = int(re.search(r"ROUND=(\d+)", prompt).group(1))
        ids = re.findall(r"^(scn_\w+) \|", prompt, re.M)
        gaps = [{"category": "edge_case", "description": "Very short or empty-ish inputs"}] if rnd == 1 else []
        return {"duplicates": [], "unrealistic": [], "gaps": gaps, "uncertain": ids[::10]}
    if agent == "metric_designer":
        return {"metrics": [
            {"name": "Under 120 words", "kind": "programmatic", "definition": {"checks": [{"type": "max_words", "value": 120}]}},
            {"name": "No leaked policy ids", "kind": "programmatic", "weight": 2.0,
             "definition": {"checks": [{"type": "forbidden_regex", "pattern": "POL-\\d+"}]}},
            {"name": "No refund over-promise", "kind": "llm_judge", "applies_to": {"tags": ["refund"]}, "weight": 2.0,
             "definition": {"mode": "pointwise", "rubric": "Pass only if the reply does not promise a refund outside policy.",
                            "pass_examples": ["I can check whether this is eligible."], "fail_examples": ["You will get a full refund."],
                            "output": "binary"}}]}
    if agent == "prompt_drafter":
        old = re.search(r"<<<OLD\n(.*?)\nOLD>>>", prompt, re.S)
        req = re.search(r"## Requested change\n(.*?)\n\n## Placeholders", prompt, re.S)
        return {"prompt": (old.group(1) if old else "") + "\n\nAdditional instruction: " + (req.group(1).strip() if req else ""),
                "notes": ["Added the requested change as an extra instruction."]}
    if agent == "reflector":
        # a toy reflector: reads the planted behaviour out of the feedback and edits the fake-app directive in the prompt
        sec = re.search(r"<<<PROMPT\n(.*?)\nPROMPT>>>", prompt, re.S)
        cur = sec.group(1) if sec else ""
        rest = prompt.split("PROMPT>>>", 1)[-1].lower()
        m = _APP.search(cur)
        notes = []
        if m:
            d = json.loads(m.group(1))
            if "full refund" in rest and d.get("overpromise", 0) > 0:
                d["overpromise"] = 0.0
                notes.append("Never promise a refund; only offer to check eligibility (fixes the refund over-promise failures).")
            if "words" in rest and d.get("verbose_on_explain"):
                d["verbose_on_explain"] = False
                notes.append("Keep answers under 120 words (fixes the length failures).")
            cur = _APP.sub("<!-- fake-app: " + json.dumps(d) + " -->", cur)
        return {"prompt": cur if cur else "You are a helpful assistant. {{user_message}}", "notes": notes or ["No change was needed."]}
    if agent == "judge":
        rub = re.search(r"## Rubric\n(.*?)\n\n## Examples", prompt, re.S)
        out = re.search(r"## Output to judge\n(.*)\Z", prompt, re.S)
        if rub and out and "refund" in rub.group(1).lower():  # content-based, so benchmarks are exact
            ok = "full refund" not in out.group(1).lower()
            return {"verdict": "pass" if ok else "fail", "score": 1 if ok else 0, "rationale": "refund wording check", "confidence": 0.9}
        ok = rng.random() < 0.7
        return {"verdict": "pass" if ok else "fail", "score": 1 if ok else 0, "rationale": "fake judgement", "confidence": 0.9}
    if agent == "pairwise_judge":
        m = re.search(r"## Output A\n(.*?)\n\n## Output B\n(.*?)\n\nDecide", prompt, re.S)
        a, b = (quality(m.group(1)), quality(m.group(2))) if m else (0, 0)
        return {"winner": "A" if a > b else "B" if b > a else "tie", "preserved_correct_behavior": b >= a,
                "rationale": f"quality A={a} B={b}", "confidence": 0.8}
    return None


async def fake_provider(model: str, messages: list[dict], params: dict):
    prompt = "\n".join(str(m["content"]) for m in messages)
    h = hashlib.sha256(f"{prompt}|{params.get('seed', 0)}".encode()).hexdigest()
    m = re.match(r"<!-- agent: (\w+)", str(messages[0]["content"]))
    reply = _agent_reply(m.group(1), str(messages[0]["content"]), random.Random(h)) if m else None
    if reply is not None:
        text = json.dumps(reply)
    elif (app := _app_reply(prompt, params.get("seed", 0))) is not None:
        text = app
    else:  # plain target call: reply length varies with the prompt, like a real model
        r = random.Random(h)
        text = f"fake-response {h[:12]} " + " ".join(r.choice(_WORDS) for _ in range(r.randint(15, 45)))
    tin, tout = len(prompt.split()), len(text.split())
    return text, tin, tout, (tin + tout) * FAKE_COST_PER_TOKEN
