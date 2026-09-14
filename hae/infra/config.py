"""Runtime configuration, resolved from the environment.

Every setting here has a defensible default except the ones that cannot: the
Google Cloud project and, for non-Vertex providers, the API key. V1 defaulted
the project to the literal string `YOUR_GCP_PROJECT_ID`, so a pod launched
without it did not fail at startup -- it ran, called Vertex, and returned

    403 Permission denied on resource project YOUR_GCP_PROJECT_ID

from deep inside a retry loop. Misconfiguration should be reported by the
thing that is misconfigured, at the moment it is read.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


class ConfigurationError(RuntimeError):
    """Raised when a required setting is absent or unusable."""


def _env(*names: str, default: Optional[str] = None) -> Optional[str]:
    """First non-empty value among `names`, else `default`."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass
class EvolutionConfig:
    """Resolved configuration for one process."""

    # Google Cloud. `project_id` is intentionally Optional here and validated
    # by `require_project()` at the point of use, so importing this module on a
    # workstation with no cloud credentials still works for unit tests.
    project_id: Optional[str] = field(
        default_factory=lambda: _env("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT"))
    location: str = field(
        default_factory=lambda: _env(
            "GOOGLE_CLOUD_LOCATION", "GCP_LOCATION", default="us-central1"))

    # Provider selection: auto | vertex | gemini_api | openai | anthropic | ollama | vllm
    llm_provider: str = field(
        default_factory=lambda: _env("LLM_PROVIDER", default="auto"))
    gemini_api_key: str = field(
        default_factory=lambda: _env("GEMINI_API_KEY", default=""))
    openai_api_key: str = field(
        default_factory=lambda: _env("OPENAI_API_KEY", default=""))
    openai_base_url: str = field(
        default_factory=lambda: _env(
            "OPENAI_BASE_URL", default="https://api.openai.com/v1"))
    anthropic_api_key: str = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY", default=""))
    ollama_base_url: str = field(
        default_factory=lambda: _env(
            "OLLAMA_BASE_URL", default="http://localhost:11434/v1"))
    vllm_base_url: str = field(
        default_factory=lambda: _env(
            "VLLM_BASE_URL", default="http://localhost:8000/v1"))

    # Model tiers.
    worker_model: str = field(
        default_factory=lambda: _env("WORKER_MODEL", default="gemini-2.5-flash"))
    executive_model: str = field(
        default_factory=lambda: _env("EXECUTIVE_MODEL", default="gemini-2.5-pro"))
    judge_model: str = field(
        default_factory=lambda: _env("JUDGE_MODEL", default="gemini-2.5-pro"))
    mutator_model: str = field(
        default_factory=lambda: _env("MUTATOR_MODEL", default="gemini-2.5-pro"))

    # Tournament hyperparameters.
    population_size: int = field(
        default_factory=lambda: int(_env("POPULATION_SIZE", default="10")))
    num_generations: int = field(
        default_factory=lambda: int(_env("NUM_GENERATIONS", default="1")))
    mutation_rate: float = field(
        default_factory=lambda: float(_env("MUTATION_RATE", default="0.85")))
    crossover_rate: float = field(
        default_factory=lambda: float(_env("CROSSOVER_RATE", default="0.40")))
    top_k_survivors: int = field(
        default_factory=lambda: int(_env("TOP_K_SURVIVORS", default="5")))

    # Output and storage.
    local_output_dir: str = field(
        default_factory=lambda: _env(
            "OUTPUT_DIR", default="/tmp/agent_evolution_outputs"))
    gcs_bucket: str = field(
        default_factory=lambda: _env("GCS_BUCKET", default=""))

    # Execution timeouts, seconds.
    department_timeout: int = field(
        default_factory=lambda: int(_env("DEPARTMENT_TIMEOUT", default="600")))
    executive_timeout: int = field(
        default_factory=lambda: int(_env("EXECUTIVE_TIMEOUT", default="600")))

    def require_project(self) -> str:
        """Returns the project id, or explains precisely what to set."""
        if not self.project_id:
            raise ConfigurationError(
                "No Google Cloud project configured. Set GOOGLE_CLOUD_PROJECT "
                "(or GCP_PROJECT) in the environment. This is not defaulted: a "
                "placeholder project turns a configuration mistake into an "
                "opaque 403 from the Vertex API an hour into a tournament."
            )
        return self.project_id

    def require_bucket(self) -> str:
        """Returns the results bucket, or explains precisely what to set."""
        if not self.gcs_bucket:
            raise ConfigurationError(
                "No GCS bucket configured. Set GCS_BUCKET in the environment. "
                "Without it a tournament runs to completion and then has "
                "nowhere to write its scorecards."
            )
        return self.gcs_bucket


DEFAULT_CONFIG = EvolutionConfig()
