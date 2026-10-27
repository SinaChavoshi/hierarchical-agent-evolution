"""V15 generational loop: evolve harness genomes (knobs + code), plus a random-search control.

Protocol per generation (successive halving):
  1. Evaluate every new genome on the `stage1` subset of the training tasks.
  2. Promote the top `promote_k` genomes (by stage-1 fitness) to the full training split.
  3. Elites = genomes evaluated on the full training split, ranked by training fitness.
  4. Breed the next population from the elites (code mutation, knob mutation, crossover).

The `random` mode is the equal-budget control: every non-seed genome is a uniform random
knob sample with no code changes, evaluated with exactly the same halving schedule.
The test split is never touched here; see `scripts/v15_evolve.py test`.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hae.evolution.evaluator import GenomeEvaluator
from hae.evolution.genome import HarnessGenome
from hae.evolution.leakage import LeakageGate
from hae.evolution.mutation import CodeMutator, crossover_patches, failure_digest
from hae.swebench.harness_knobs import crossover_knobs, mutate_knobs, sample_knobs


@dataclass
class EvolutionConfig:
    mode: str = "evolve"  # evolve | random
    population: int = 8
    generations: int = 6
    promote_k: int = 3
    token_lambda: float = 0.05
    partial_weight: float = 0.25
    p_code: float = 0.5
    p_knob: float = 0.3
    p_cross: float = 0.2
    seed: int = 15
    stage1_tasks: List[str] = field(default_factory=list)
    train_tasks: List[str] = field(default_factory=list)


class EvolutionRun:
    def __init__(self, cfg: EvolutionConfig, evaluator: GenomeEvaluator, out_root: str,
                 code_mutator: Optional[CodeMutator] = None, leakage: Optional[LeakageGate] = None,
                 logger: Callable[[str], None] = print) -> None:
        self.cfg = cfg
        self.evaluator = evaluator
        self.out_root = os.path.abspath(out_root)
        self.code_mutator = code_mutator
        self.leakage = leakage
        self.logger = logger
        self.rng = random.Random(cfg.seed)
        self.genomes: Dict[str, HarnessGenome] = {}
        self.generation = 0
        self.history: List[Dict[str, Any]] = []
        self.state_path = os.path.join(self.out_root, "state.json")
        os.makedirs(self.out_root, exist_ok=True)
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not os.path.exists(self.state_path):
            return
        with open(self.state_path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        self.genomes = {gid: HarnessGenome.from_dict(g) for gid, g in st.get("genomes", {}).items()}
        self.generation = int(st.get("generation", 0))
        self.history = list(st.get("history") or [])
        if st.get("rng_state"):
            v, internal, gauss = st["rng_state"]
            self.rng.setstate((v, tuple(internal), gauss))
        self.logger(f"[v15] resumed {len(self.genomes)} genome(s) at generation {self.generation}")

    def save(self) -> None:
        st = {
            "config": self.cfg.__dict__,
            "generation": self.generation,
            "genomes": {gid: g.to_dict() for gid, g in self.genomes.items()},
            "history": self.history,
            "rng_state": [self.rng.getstate()[0], list(self.rng.getstate()[1]), self.rng.getstate()[2]],
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1)
        os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------ #
    def fitness(self, g: HarnessGenome, tasks: List[str]) -> float:
        return g.fitness(tasks, token_lambda=self.cfg.token_lambda, partial_weight=self.cfg.partial_weight)

    def elites(self) -> List[HarnessGenome]:
        full = [g for g in self.genomes.values() if g.evaluated_on(self.cfg.train_tasks)]
        return sorted(full, key=lambda g: (self.fitness(g, self.cfg.train_tasks), -g.patch_lines), reverse=True)

    def _new_id(self, gen: int, idx: int) -> str:
        return f"{self.cfg.mode[:3]}_g{gen}_{idx:02d}"

    def _pick_parent(self, pool: List[HarnessGenome]) -> HarnessGenome:
        k = min(3, len(pool))
        contenders = self.rng.sample(pool, k)
        return max(contenders, key=lambda g: self.fitness(g, self.cfg.train_tasks))

    def _make_child(self, gen: int, idx: int, pool: List[HarnessGenome]) -> Optional[HarnessGenome]:
        cid = self._new_id(gen, idx)
        if self.cfg.mode == "random":
            return HarnessGenome(genome_id=cid, generation=gen, origin="random", knobs=sample_knobs(self.rng))
        r = self.rng.random()
        if r < self.cfg.p_code and self.code_mutator is not None:
            parent = self._pick_parent(pool)
            logs = {t: self.evaluator.log_tail(parent.genome_id, t) for t in parent.results}
            digest = failure_digest(parent, logs)
            res = self.code_mutator.propose(parent, cid, digest, self.rng)
            if res is not None:
                return HarnessGenome(genome_id=cid, generation=gen, parent_ids=[parent.genome_id],
                                     origin="mutate_code", knobs=dict(parent.knobs), patch=res.patch,
                                     notes=[f"targets={res.targets}", f"attempts={res.attempts}",
                                            f"rationale={res.rationale}"])
            self.logger(f"[v15] {cid}: code mutation failed validation; falling back to knob mutation")
            return HarnessGenome(genome_id=cid, generation=gen, parent_ids=[parent.genome_id],
                                 origin="mutate_knobs", knobs=mutate_knobs(parent.knobs, self.rng),
                                 patch=parent.patch, notes=["fallback after failed code mutation"])
        if r < self.cfg.p_code + self.cfg.p_knob or len(pool) < 2:
            parent = self._pick_parent(pool)
            return HarnessGenome(genome_id=cid, generation=gen, parent_ids=[parent.genome_id],
                                 origin="mutate_knobs", knobs=mutate_knobs(parent.knobs, self.rng),
                                 patch=parent.patch)
        a, b = self.rng.sample(pool, 2)
        patch = crossover_patches(self.evaluator.worktrees, a, b, cid, self.leakage) if self.leakage else a.patch
        return HarnessGenome(genome_id=cid, generation=gen, parent_ids=[a.genome_id, b.genome_id],
                             origin="crossover", knobs=crossover_knobs(a.knobs, b.knobs, self.rng), patch=patch)

    def _dedupe(self, child: HarnessGenome) -> bool:
        return any(g.content_hash == child.content_hash for g in self.genomes.values())

    # ------------------------------------------------------------------ #
    def _evaluate_generation(self, gen: int) -> None:
        members = [g for g in self.genomes.values() if g.generation == gen]
        self.evaluator.evaluate(members, self.cfg.stage1_tasks)
        self.save()
        ranked = sorted(members, key=lambda g: self.fitness(g, self.cfg.stage1_tasks), reverse=True)
        promote = ranked[: self.cfg.promote_k]
        seed = self.genomes.get("seed")
        if gen == 0 and seed is not None and seed not in promote:
            promote.append(seed)  # the hand-designed baseline is always fully evaluated
        self.evaluator.evaluate(promote, self.cfg.train_tasks)
        self.save()
        el = self.elites()
        rec = {
            "generation": gen,
            "members": [g.genome_id for g in members],
            "stage1": {g.genome_id: round(g.success_rate(self.cfg.stage1_tasks), 3) for g in members},
            "promoted": [g.genome_id for g in promote],
            "elites": [{"genome_id": g.genome_id, "origin": g.origin,
                        "train_success": round(g.success_rate(self.cfg.train_tasks), 3),
                        "train_fitness": round(self.fitness(g, self.cfg.train_tasks), 4),
                        "mean_tokens": int(g.mean_tokens(self.cfg.train_tasks)), "patch_lines": g.patch_lines}
                       for g in el[:5]],
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.history = [h for h in self.history if h.get("generation") != gen] + [rec]
        self.save()
        best = el[0] if el else None
        self.logger(f"[v15] generation {gen} done; best={best.genome_id if best else None} "
                    f"train={best.success_rate(self.cfg.train_tasks) if best else 0:.3f}")

    def run(self) -> None:
        if "seed" not in self.genomes:
            self.genomes["seed"] = HarnessGenome(genome_id="seed", generation=0, origin="seed")
            self.save()
        # The hand-designed seed is evaluated on the full training split first: it is the
        # baseline every genome is compared against, and its failures seed the first code mutations.
        self.evaluator.evaluate([self.genomes["seed"]], self.cfg.train_tasks)
        self.save()
        while self.generation < self.cfg.generations:
            gen = self.generation
            members = [g for g in self.genomes.values() if g.generation == gen]
            pool = self.elites() or [self.genomes["seed"]]
            idx = len(members)
            attempts = 0
            while len(members) < self.cfg.population and attempts < self.cfg.population * 3:
                attempts += 1
                child = self._make_child(gen, idx, pool[:4])
                idx += 1
                if child is None or self._dedupe(child):
                    continue
                self.genomes[child.genome_id] = child
                members.append(child)
                self.save()
                self.logger(f"[v15] gen {gen}: created {child.genome_id} ({child.origin}, "
                            f"patch={child.patch_lines} lines)")
            self._evaluate_generation(gen)
            self.generation = gen + 1
            self.save()
        self.logger("[v15] evolution complete")
