"""All tunables (PLAN.md section 16). Override via env: PV_<NAME>, or a .env file."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PV_", env_file=(str(Path(__file__).resolve().parents[2] / ".env"), ".env"), extra="ignore")

    db_url: str = "sqlite:///workspace.db"
    api_token: str | None = None  # optional simple token (M9)
    static_dir: str | None = None  # built frontend served by app.web (default: <repo>/frontend/dist)

    # models (LiteLLM names). Use "fake/<anything>" for the deterministic mock provider.
    target_model: str = "gemini/gemini-2.5-flash"
    judge_model: str = "gemini/gemini-2.5-flash"

    judge_reasoning: str = "disable"  # provider thinking for judges (default|disable|low|medium|high); see core/reasoning.py
    generator_reasoning: str = "disable"  # scenario generator and critic
    target_reasoning: str = "default"  # the model under test: leave as your app runs it
    embedding_model: str = "all-MiniLM-L6-v2"  # "hash" = no-dependency fallback (used in tests)
    llm_concurrency: int = 5
    scenarios_per_diff_change: int = 8
    gap_fill_n: int = 5
    cluster_target_size: int = 8

    scenarios_per_category_initial: int = 25
    max_critic_rounds: int = 2
    dedupe_cosine_threshold: float = 0.92
    review_queue_max_per_cluster: int = 2
    smoke_fraction: float = 0.10
    smoke_repeats: int = 2
    default_repeats: int = 3
    max_repeats_adaptive: int = 5
    priority_weights: dict[str, float] = {
        "diff_relevance": 0.35, "severity": 0.30, "uniqueness": 0.20,
        "pinned_or_flagged": 0.10, "disputed_potential": 0.05,
    }
    severity_protected_threshold: int = 4
    tiers: dict[str, float] = {"full": 1.0, "balanced": 0.6, "lean": 0.3}
    cost_high_percentile: float = 0.90
    probe_scenarios: int = 5
    heldout_fraction: float = 0.30
    judge_temperature: float = 0.0
    target_temperature: float = 0.7  # approximates a typical app setting
    calibration_max_items: int = 30
    tier_repeats: dict[str, int] = {"full": 3, "balanced": 3, "lean": 2}
    # Lever heuristics (ponytail: replaced by measured rates once M5 runs exist)
    confirm_flagged: bool = False  # keep running a case that is already flagged (by the exact, corrected test) up to the max repeats
    noise_prior_var: float = 0.05  # smoothing: a small smoke sample with no flips must not claim zero noise
    noise_prior_n: int = 5
    noisy_band_cut: float = 0.5  # a band this wide cannot resolve a flip: within-band cases become Inconclusive
    pair_noise_floor: float = 0.05
    bootstrap_n: int = 2000
    bootstrap_seed: int = 0
    deep_dive_per_regression: int = 3
    deep_dive_max_scenarios: int = 12
    smoke_min: int = 3
    gepa_max_metric_calls: int = 120  # one metric call = one scenario scored for one candidate
    gepa_minibatch: int = 3
    gepa_val_fraction: float = 0.30  # of the non-held-out scenarios; GEPA's own validation set for choosing candidates
    gepa_min_pool: int = 10  # hard minimum number of scenarios (train + val + a held-out set)
    gepa_reviewed_min: int = 30  # restrict to approved scenarios only if there are at least this many; fewer would make a useless split
    cheap_judge_model: str | None = None  # default derived from the judge model's provider (see cheap_model_for)
    judge_ensemble_model: str | None = None  # second judge for rubrics that opt in (JudgeDef.ensemble)
    regression_probe_n: int = 6  # old-prompt probe size for regression_guard scenarios
    cheap_judge_cost_ratio: float = 0.3  # cheap judge costs this fraction of the default judge
    easy_rubric_max_chars: int = 400
    adaptive_stable_share: float = 0.6  # share of cases that stop after 2 repeats
    significance_alpha: float = 0.05


settings = Settings()
