"""Task specification: what a firm is asked to do, and on what terms.

This layer exists because the long-term goal -- a user supplies a goal and the
platform pursues it -- requires the objective, the way it is checked, the spend
limit and the action space to be *parameters* rather than constants. In V1 all
four were hardcoded in four different places.

Depends on `hae.evaluation` (to wrap the existing harness and benchmark) and on
nothing else in the package. Nothing in `hae.evaluation` may import from here,
so the dependency stays one-way.
"""

from hae.task.budget import Budget, BudgetExceeded, Charge
from hae.task.spec import (
    ALL_CAPABILITIES,
    CAPABILITY_READ,
    CAPABILITY_SHELL,
    CAPABILITY_VERIFY,
    CAPABILITY_WRITE,
    DEFAULT_CAPABILITIES,
    LEGACY_OBJECTIVE,
    Task,
    TaskError,
    legacy_task,
)
from hae.task.verifier import (
    BenchmarkVerifier,
    CompositeVerifier,
    ExecutionGateVerifier,
    NullVerifier,
    Submission,
    VerificationOutcome,
    Verifier,
)

__all__ = [
    "ALL_CAPABILITIES", "Budget", "BudgetExceeded", "BenchmarkVerifier",
    "CAPABILITY_READ", "CAPABILITY_SHELL", "CAPABILITY_VERIFY",
    "CAPABILITY_WRITE", "Charge", "CompositeVerifier", "DEFAULT_CAPABILITIES",
    "ExecutionGateVerifier", "LEGACY_OBJECTIVE", "NullVerifier", "Submission",
    "Task", "TaskError", "VerificationOutcome", "Verifier", "legacy_task",
]
