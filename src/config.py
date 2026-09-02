"""Global configuration for Hierarchical Agent Evolution."""

import os
from dataclasses import dataclass

@dataclass
class EvolutionConfig:
    # Google Cloud Project settings
    project_id: str = os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("GCP_PROJECT", "gemle-gke-dev"))
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", os.getenv("GCP_LOCATION", "us-central1"))
    
    # Model defaults on Vertex AI
    worker_model: str = os.getenv("WORKER_MODEL", "gemini-2.5-flash")
    executive_model: str = os.getenv("EXECUTIVE_MODEL", "gemini-2.5-pro")
    judge_model: str = os.getenv("JUDGE_MODEL", "gemini-2.5-pro")
    mutator_model: str = os.getenv("MUTATOR_MODEL", "gemini-2.5-pro")

    # Evolutionary tournament hyperparameters
    population_size: int = int(os.getenv("POPULATION_SIZE", "4"))
    num_generations: int = int(os.getenv("NUM_GENERATIONS", "3"))
    mutation_rate: float = float(os.getenv("MUTATION_RATE", "0.85"))
    crossover_rate: float = float(os.getenv("CROSSOVER_RATE", "0.40"))
    top_k_survivors: int = int(os.getenv("TOP_K_SURVIVORS", "2"))
    
    # Output and storage paths
    local_output_dir: str = os.getenv("OUTPUT_DIR", "/tmp/agent_evolution_outputs")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "gemle-gke-dev-agent-evolution")
    
    # Execution timeouts (seconds)
    department_timeout: int = int(os.getenv("DEPARTMENT_TIMEOUT", "600"))
    executive_timeout: int = int(os.getenv("EXECUTIVE_TIMEOUT", "600"))

DEFAULT_CONFIG = EvolutionConfig()
