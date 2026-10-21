# 1. What Prompt Mortem does

## The problem

You change a prompt to fix one thing, for example "answers are too long". The new prompt fixes that. But did it also stop following a rule, drop a language, or start promising refunds? Checking by hand means reading dozens of outputs. Normal automated tests need a labelled dataset that most teams do not have.

There is a second problem. Language models give different answers each time. A difference you see between old and new might be pure chance. A tool that ignores this will cry wolf, or miss real problems.

## What the tool gives you

Prompt Mortem answers two questions with evidence:

1. **Did the change fix what I wanted?**
2. **Did it break anything else?**

It does this without a dataset. AI agents propose test cases and checks, you steer them, the tool runs both prompts on the same inputs many times, and statistics decide which differences are real.

## The eight steps, in plain words

| Step | What happens | Who decides |
|------|--------------|-------------|
| 1. Setup | You describe the app and its rules, and give the old and new prompt. The tool can draft the new prompt for you. | You |
| 2. Understand | Agents read your app and the difference between the prompts, and list what each change could affect. | You can correct it |
| 3. Scenarios | The tool plans test inputs for the goal, edge cases, and things you did not touch. It groups similar ones. | You review the top cases |
| 4. Metrics | The tool designs the checks. Checks that code can do (length, patterns, format) come first. AI judges handle the rest. | You approve each metric |
| 5. Budget | Only now does it estimate cost. It shows three plan sizes and what each gives up. | You pick a size and a hard cap |
| 6. Run | Old and new run on the same inputs, plus the old prompt against itself to measure randomness. | The tool, within your cap |
| 7. Report | A verdict, a per-metric breakdown, examples, and a scope stamp. | You can override single cases |
| 8. Refine | Optional. The tool searches for a better prompt and checks it on cases it never showed the search. | You accept, edit or reject |

## The rules the tool never breaks

These come from `CLAUDE.md` and shape every design choice.

1. **People stay in the loop** at scenario, metric, budget and result stages. What you approve or reject is saved and fed back into the agents.
2. **You review only the top cases**: cluster representatives, cases the critic is unsure about, and the highest-impact case per cluster. Rejecting one case can reject its whole cluster.
3. **Cost comes after planning.** The plan is made exhaustively first. Only then is it priced and trimmed, and every size shows the coverage it loses.
4. **Results are paired and noise-adjusted.** Old and new are compared on the same inputs and repeats, and old-vs-old noise is always measured.
5. **Every report carries a scope stamp.** It states the tier, coverage, how much a human reviewed, how much is verified versus synthetic, and the noise. The tool never claims more than was tested.
6. **Code checks run before AI judges, and judges are blind.** A judge never learns which prompt wrote which output.
7. **A dataset is optional.** Evaluation platforms (Promptfoo) are optional adapters, not requirements.
8. **A hard budget cap pauses and asks.** It never silently cuts the work.

## A worked example

The bundled example is a billing support bot (`examples/billing_bot/`).

- The **old prompt** is friendly and thorough, so replies are long.
- The **new prompt** says "under 120 words, direct and warm", and keeps the rules about policy IDs, language and refunds.
- **What you would hope to see:** "Under 120 words" is fixed, and the refund, policy-ID and language checks are unchanged.
- **If someone shortened the prompt too far** and dropped the refund rule, the report should show broken refund and language checks, point at the exact cases (a Spanish message, a message asking about `POL-4471`), and say BLOCK.

## What it does not do (version 1)

- Single-turn text prompts only. No multi-turn chats, tool calls or agents.
- Single user, running locally. There is an optional shared token, not accounts.
- Custom metrics are declarative (a list of checks), so no arbitrary code runs.
- The tool measures your prompt on the model you choose. It does not prove the same result on a different model.
