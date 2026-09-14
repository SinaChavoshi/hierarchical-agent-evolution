"""Hierarchical Agent Evolution.

An evolutionary platform in which populations of hierarchical LLM agent
organisations compete on a task, are scored partly by machine-verified
execution of the code they produce, and are bred forward.

Subpackages:
    genome        Heritable organisation structure: schema, breeding, mutation,
                  morphogenesis.
    runtime       Executing a single firm: the company runner and its workspace.
    evaluation    Scoring a firm: the execution harness, artifact accounting,
                  the LLM judge, and the composite fitness function.
    infra         Cross-cutting services: LLM access, configuration, telemetry.
    orchestration Running a tournament: the local engine and the pod worker.
"""

__version__ = "2.0.0.dev0"
