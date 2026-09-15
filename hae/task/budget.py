"""Spend limits that are actually enforced.

V1 had a `budget_usd` field on every genome. It was read in exactly one place --
to compute a *score penalty* after the run finished (`company.py`, the
`cost_penalty` / `efficiency_bonus` block). A firm that blew its budget by 10x
lost some points. It did not stop.

That is a scoring opinion, not a spend limit, and it is only survivable because
a human watches every launch. The moment generations run unattended, or the
objective comes from someone else, an unbounded loop is an unbounded bill.

The reserve
-----------
A naive hard cap has a failure mode: the firm spends everything on departmental
work and has nothing left for the CEO's final synthesis, so the run produces no
deliverable and scores zero. The work was done and then thrown away.

So the cap has two tiers. Ordinary calls are refused once spend passes
`limit * (1 - reserve_fraction)`; calls marked `reserved=True` -- the final
synthesis -- may draw on the remainder. A firm that overruns therefore returns a
degraded deliverable rather than nothing at all, and the scorecard says which.
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_RESERVE_FRACTION = 0.10


class BudgetExceeded(RuntimeError):
    """Raised only by `charge(..., strict=True)`. The default path refuses
    quietly and lets the caller degrade."""


@dataclass
class Charge:
    """One debit, kept so a scorecard can show where the money went."""

    label: str
    usd: float
    reserved: bool = False


@dataclass
class Budget:
    """A spend ceiling for one firm's run.

    Thread-safe: departmental pods run concurrently in a ThreadPoolExecutor, so
    two agents can bill at the same moment. An unsynchronised check-then-charge
    would let both through.
    """

    limit_usd: float
    reserve_fraction: float = DEFAULT_RESERVE_FRACTION
    # Secondary ceilings. A cheap model in a tight loop can burn wall-clock and
    # quota without approaching a dollar limit.
    max_calls: Optional[int] = None

    spent_usd: float = 0.0
    calls: int = 0
    refusals: int = 0
    charges: List[Charge] = field(default_factory=list)
    _lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.limit_usd <= 0:
            raise ValueError(f"Budget.limit_usd must be positive, got {self.limit_usd}")
        if not 0.0 <= self.reserve_fraction < 1.0:
            raise ValueError(
                f"Budget.reserve_fraction must be in [0, 1), got {self.reserve_fraction}")

    # ---------------------------------------------------------------- #
    # Ceilings
    # ---------------------------------------------------------------- #

    @property
    def working_limit_usd(self) -> float:
        """What ordinary calls may spend, holding back the synthesis reserve."""
        return self.limit_usd * (1.0 - self.reserve_fraction)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        """True when even a reserved call would be refused."""
        return self.spent_usd >= self.limit_usd or self._calls_exhausted

    @property
    def working_exhausted(self) -> bool:
        """True when ordinary calls should stop. The reserve may remain."""
        return self.spent_usd >= self.working_limit_usd or self._calls_exhausted

    @property
    def _calls_exhausted(self) -> bool:
        return self.max_calls is not None and self.calls >= self.max_calls

    @property
    def overrun(self) -> bool:
        """Whether anything was ever refused. Distinct from `exhausted`: a run
        can finish exactly at the line without ever having been stopped."""
        return self.refusals > 0

    # ---------------------------------------------------------------- #
    # Spending
    # ---------------------------------------------------------------- #

    def can_spend(self, reserved: bool = False) -> bool:
        with self._lock:
            return not (self.exhausted if reserved else self.working_exhausted)

    def charge(self, usd: float, label: str = "", reserved: bool = False,
               strict: bool = False) -> bool:
        """Debits `usd`. Returns False if the call should not have happened.

        Charging happens *after* the spend, because a token count is not known
        until the response arrives. The ceiling is therefore enforced by
        `can_spend` before the call; `charge` records reality and can overshoot
        the limit by at most one call. That residual is bounded and visible,
        which is the best available property without pre-estimating every
        prompt.
        """
        if usd < 0:
            raise ValueError(f"charge must be non-negative, got {usd}")
        with self._lock:
            blocked = self.exhausted if reserved else self.working_exhausted
            if blocked:
                self.refusals += 1
                if strict:
                    raise BudgetExceeded(
                        f"{label or 'call'} refused: spent ${self.spent_usd:.4f} "
                        f"of ${self.limit_usd:.4f}"
                        + (f", {self.calls}/{self.max_calls} calls"
                           if self.max_calls else ""))
                return False
            self.spent_usd += usd
            self.calls += 1
            self.charges.append(Charge(label=label, usd=usd, reserved=reserved))
            return True

    def refuse(self, label: str = "") -> None:
        """Records that a call was declined before being made."""
        with self._lock:
            self.refusals += 1

    # ---------------------------------------------------------------- #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "limit_usd": round(self.limit_usd, 6),
            "working_limit_usd": round(self.working_limit_usd, 6),
            "spent_usd": round(self.spent_usd, 6),
            "remaining_usd": round(self.remaining_usd, 6),
            "calls": self.calls,
            "max_calls": self.max_calls,
            "refusals": self.refusals,
            "overrun": self.overrun,
            "exhausted": self.exhausted,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Budget":
        return cls(
            limit_usd=float(data["limit_usd"]),
            reserve_fraction=float(data.get("reserve_fraction",
                                            DEFAULT_RESERVE_FRACTION)),
            max_calls=data.get("max_calls"),
        )

    @classmethod
    def unlimited(cls) -> "Budget":
        """An explicit escape hatch, so that "no cap" is a visible choice in a
        config rather than the silent default it used to be."""
        return cls(limit_usd=float("inf"), reserve_fraction=0.0)
