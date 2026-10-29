"""V15–V17 generational loop: evolve harness genomes (knobs + code + org + red team), plus a random-search control.

Protocol per generation (successive halving):
  1. Evaluate every new genome on the `stage1` subset of the training tasks.
  2. Promote the top `promote_k` genomes (by stage-1 fitness) to the full training split.
  3. Elites = genomes evaluated on the full training split, ranked by training fitness.
  4. Breed the next population from the elites (code mutation, knob mutation, crossover, org/red-team mutation).
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
import hae.evolution.org_evolution as orgevo
from hae.swebench.harness_knobs import crossover_knobs, mutate_knobs, sample_knobs
from hae.swebench.org_binding import crossover_redteam, mutate_redteam, sample_redteam


@dataclass
class EvolutionConfig:
    mode: str = "evolve"  # evolve | random
    population: int = 8
    generations: int = 6
    promote_k: int = 3
    token_lambda: float = 0.05
    partial_weight: float = 0.25
    redteam_weight: float = 0.0  # V17: weight on balanced red-team verdict accuracy (2x false-PASS penalty)
    p_code: float = 0.5
    p_knob: float = 0.3
    p_cross: float = 0.2
    p_org: float = 0.0      # V16: probability of an organization mutation
    p_redteam: float = 0.0  # V17: probability of a red-team gene or red-team role mutation
    seed: int = 15
    stage1_tasks: List[str] = field(default_factory=list)
    train_tasks: List[str] = field(default_factory=list)


class EvolutionRun:
    def __init__(self, cfg: EvolutionConfig, evaluator: GenomeEvaluator, out_root: str,
                 code_mutator: Optional[CodeMutator] = None, leakage: Optional[LeakageGate] = None,
                 logger: Callable[[str], None] = print, seed_genome: Optional[HarnessGenome] = None,
                 org_mutator: Optional["orgevo.OrgMutator"] = None) -> None:
        self.cfg = cfg
        self.evaluator = evaluator
        self.out_root = os.path.abspath(out_root)
        self.code_mutator = code_mutator
        self.leakage = leakage
        self.logger = logger
        self.seed_genome = seed_genome
        self.org_mutator = org_mutator
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
        return g.fitness(
            tasks,
            token_lambda=self.cfg.token_lambda,
            partial_weight=self.cfg.partial_weight,
            redteam_weight=self.cfg.redteam_weight,
        )

    def elites(self) -> List[HarnessGenome]:
        full = [g for g in self.genomes.values() if g.evaluated_on(self.cfg.train_tasks)]
        return sorted(full, key=lambda g: (self.fitness(g, self.cfg.train_tasks), -g.patch_lines), reverse=True)

    def _new_id(self, gen: int, idx: int) -> str:
        return f"{self.cfg.mode[:3]}_g{gen}_{idx:02d}"

    def _pick_parent(self, pool: List[HarnessGenome]) -> HarnessGenome:
        k = min(3, len(pool))
        contenders = self.rng.sample(pool, k)
        return max(contenders, key=lambda g: self.fitness(g, self.cfg.train_tasks))

    def _org_of(self, parent: HarnessGenome) -> Dict[str, Any]:
        """Parent's organization and red-team genes with role credits from its training runs folded in."""
        roles = parent.roles
        if parent.has_org and parent.roles:
            recs = self.evaluator.org_records(parent.genome_id, self.cfg.train_tasks)
            roles = orgevo.apply_role_stats(parent.roles, orgevo.aggregate_role_stats(recs))
        out: Dict[str, Any] = {
            "org_mode": parent.org_mode,
            "roles": [dict(r) for r in roles],
            "ceo_policy": dict(parent.ceo_policy),
        }
        if parent.has_redteam:
            out["redteam"] = dict(parent.redteam)
        return out

    def _child(self, cid: str, gen: int, parents: List[HarnessGenome], origin: str, knobs: Dict[str, Any],
               patch: str, org: Optional[Dict[str, Any]] = None, notes: Optional[List[str]] = None) -> HarnessGenome:
        org = org if org is not None else self._org_of(parents[0])
        return HarnessGenome(genome_id=cid, generation=gen, parent_ids=[p.genome_id for p in parents], origin=origin,
                             knobs=knobs, patch=patch, notes=list(notes or []), **org)

    def _make_redteam_child(self, cid: str, gen: int, pool: List[HarnessGenome]) -> HarnessGenome:
        parent = self._pick_parent(pool)
        org = self._org_of(parent)
        recs = self.evaluator.org_records(parent.genome_id, self.cfg.train_tasks)
        stats = orgevo.aggregate_role_stats(recs)
        rt_dig = orgevo.redteam_digest(org["roles"], stats, recs)
        if self.org_mutator is not None and org["roles"] and self.rng.random() < 0.45:
            roles, note = self.org_mutator.redteam_rewrite(org["roles"], rt_dig, self.rng, gen)
            if roles is not None:
                assert orgevo.generalists_present(roles), "generalists must never be removed"
                org["roles"] = roles
                self.logger(f"[v17] {cid}: {note}")
                return self._child(cid, gen, [parent], "redteam_rewrite", dict(parent.knobs), parent.patch, org, [note])
        new_rt, note = mutate_redteam(org.get("redteam") or {}, self.rng, n_genes=self.rng.randint(1, 2))
        org["redteam"] = new_rt
        self.logger(f"[v17] {cid}: {note}")
        return self._child(cid, gen, [parent], "redteam_mutate", dict(parent.knobs), parent.patch, org, [note])

    def _make_org_child(self, cid: str, gen: int, pool: List[HarnessGenome]) -> Optional[HarnessGenome]:
        parent = self._pick_parent(pool)
        org = self._org_of(parent)
        recs = self.evaluator.org_records(parent.genome_id, self.cfg.train_tasks)
        digest = orgevo.org_digest(org["roles"], orgevo.aggregate_role_stats(recs), recs)
        r = self.rng.random()
        roles, note = None, ""
        if r < 0.35 and self.org_mutator is not None:
            roles, note = self.org_mutator.role_rewrite(org["roles"], digest, self.rng, gen)
        elif r < 0.55 and self.org_mutator is not None:
            roles, note = self.org_mutator.role_add(org["roles"], digest, self.rng, gen)
        elif r < 0.70:
            roles, note = orgevo.role_remove(org["roles"], self.rng)
        if roles is None:
            pol, mode, note2 = orgevo.ceo_mutate(org["ceo_policy"], org["org_mode"], self.rng)
            org.update(ceo_policy=pol, org_mode=mode)
            note = (note + "; " if note else "") + note2
            origin = "mutate_ceo"
        else:
            assert orgevo.generalists_present(roles), "generalists must never be removed"
            org["roles"] = roles
            origin = note.split(" ", 1)[0]
        self.logger(f"[v16] {cid}: {note}")
        return self._child(cid, gen, [parent], origin, dict(parent.knobs), parent.patch, org, [note])

    def _make_child(self, gen: int, idx: int, pool: List[HarnessGenome]) -> Optional[HarnessGenome]:
        cid = self._new_id(gen, idx)
        seed = self.genomes.get("seed")
        if self.cfg.mode == "random":
            knobs = sample_knobs(self.rng)
            rt = sample_redteam(self.rng) if (seed is not None and seed.has_redteam) else {}
            if seed is not None and seed.has_org and seed.roles:
                roles, pol, mode = orgevo.random_org(seed.roles, seed.ceo_policy, self.rng)
                return HarnessGenome(genome_id=cid, generation=gen, origin="random", knobs=knobs, patch=seed.patch,
                                     org_mode=mode, roles=roles, ceo_policy=pol, redteam=rt)
            return HarnessGenome(genome_id=cid, generation=gen, origin="random", knobs=knobs,
                                 patch=seed.patch if seed is not None else "", redteam=rt)
        has_rt = any(g.has_redteam for g in pool)
        has_org = any(g.has_org and g.roles for g in pool)
        u = self.rng.random()
        if has_rt and self.cfg.p_redteam > 0 and u < self.cfg.p_redteam:
            return self._make_redteam_child(cid, gen, pool)
        if has_org and self.cfg.p_org > 0 and u < self.cfg.p_redteam + self.cfg.p_org:
            return self._make_org_child(cid, gen, pool)
        # Remaining probability mass is split among code / knob / crossover as in V15.
        r = self.rng.random() * (self.cfg.p_code + self.cfg.p_knob + self.cfg.p_cross)
        if r < self.cfg.p_code and self.code_mutator is not None:
            parent = self._pick_parent(pool)
            logs = {t: self.evaluator.log_tail(parent.genome_id, t) for t in parent.results}
            digest = failure_digest(parent, logs)
            res = self.code_mutator.propose(parent, cid, digest, self.rng)
            if res is not None:
                return self._child(cid, gen, [parent], "mutate_code", dict(parent.knobs), res.patch,
                                   notes=[f"targets={res.targets}", f"attempts={res.attempts}",
                                          f"rationale={res.rationale}"])
            self.logger(f"[v15] {cid}: code mutation failed validation; falling back to knob mutation")
            return self._child(cid, gen, [parent], "mutate_knobs", mutate_knobs(parent.knobs, self.rng),
                               parent.patch, notes=["fallback after failed code mutation"])
        if r < self.cfg.p_code + self.cfg.p_knob or len(pool) < 2:
            parent = self._pick_parent(pool)
            return self._child(cid, gen, [parent], "mutate_knobs", mutate_knobs(parent.knobs, self.rng), parent.patch)
        a, b = self.rng.sample(pool, 2)
        patch = crossover_patches(self.evaluator.worktrees, a, b, cid, self.leakage) if self.leakage else a.patch
        org = self._org_of(a)
        notes: List[str] = []
        if a.has_org and b.has_org and a.roles and b.roles:
            roles, pol, note = orgevo.org_crossover(org["roles"], self._org_of(b)["roles"], org["ceo_policy"],
                                                    b.ceo_policy, self.rng)
            org.update(roles=roles, ceo_policy=pol)
            notes.append(note)
        if a.has_redteam and b.has_redteam:
            org["redteam"] = crossover_redteam(a.redteam, b.redteam, self.rng)
            notes.append("redteam_crossover")
        return self._child(cid, gen, [a, b], "crossover", crossover_knobs(a.knobs, b.knobs, self.rng), patch, org, notes)

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
                        "red_team_score": round(g.red_team_score(self.cfg.train_tasks), 4),
                        "red_team_confusion": list(g.red_team_confusion(self.cfg.train_tasks)),
                        "mean_tokens": int(g.mean_tokens(self.cfg.train_tasks)), "patch_lines": g.patch_lines}
                       for g in el[:5]],
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.history = [h for h in self.history if h.get("generation") != gen] + [rec]
        self.save()
        best = el[0] if el else None
        self.logger(f"[v15] generation {gen} done; best={best.genome_id if best else None} "
                    f"train={best.success_rate(self.cfg.train_tasks) if best else 0:.3f} "
                    f"rt={best.red_team_score(self.cfg.train_tasks) if best else 0:.3f}")

    def run(self) -> None:
        if "seed" not in self.genomes:
            if self.seed_genome is not None:
                sg = HarnessGenome.from_dict({**self.seed_genome.to_dict(), "genome_id": "seed", "generation": 0,
                                              "origin": "seed", "results": {}})
                sg.results = dict(self.seed_genome.results)
                self.genomes["seed"] = sg
            else:
                self.genomes["seed"] = HarnessGenome(genome_id="seed", generation=0, origin="seed")
            self.save()
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
                child = self. _make_child(gen, idx, pool[:4])
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
