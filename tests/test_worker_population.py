"""How the worker reads a population file.

The breeder writes an envelope; the worker indexed the envelope as if it were
a list. Every unit test passed and the campaign died in-cluster with
`KeyError: 0` -- the sixth interface disagreement between two real components
found in a single session, and the third that no mock could have caught.

The guard that was supposed to prevent it made the bug worse. `firm_index <
len(raw)` on a five-key dict compares against five, so indices 0-4 passed a
bounds check that had not located the population at all.
"""

import json
import os
import tempfile
import unittest

from hae.orchestration.worker import evaluate_single_firm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _genome(company_id="firm_a"):
    with open(os.path.join(REPO, "templates", "default_company.json")) as fh:
        g = json.load(fh)
    g["company_id"] = company_id
    return g


class PopulationShapeTest(unittest.TestCase):
    """Loading stops before any LLM call, so these run offline.

    Each case drives the real `evaluate_single_firm` and asserts on how far it
    gets: a shape that should load must get past loading, and a shape that
    should be refused must raise before anything is spent.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write(self, payload):
        p = os.path.join(self.tmp, "pop.json")
        with open(p, "w") as fh:
            json.dump(payload, fh)
        return p

    def _load_only(self, path, index=0):
        """Runs the loader and reports the genome it selected.

        `evaluate_single_firm` goes on to call an LLM, which we neither want
        nor can do here, so this stops at the first thing that needs one and
        reports what was loaded up to that point.
        """
        import hae.orchestration.worker as w
        seen = {}
        real = w.HierarchicalCompanyRunner

        class Stop(Exception):
            pass

        class Spy:
            def __init__(self, genome, **kw):
                seen["company_id"] = genome.company_id
                raise Stop()

        w.HierarchicalCompanyRunner = Spy
        try:
            evaluate_single_firm(
                generation=999, firm_index=index, region="us-east4",
                population_file=path, output_dir=self.tmp,
                objective="x")
        except Stop:
            pass
        finally:
            w.HierarchicalCompanyRunner = real
        return seen.get("company_id")

    def test_breeder_envelope_loads(self):
        """The shape `Breeder.write` actually produces. This is the
        regression."""
        path = self._write({"generation": 1, "name": "n", "objective": "",
                            "benchmark_task": "",
                            "population": [_genome("envelope_firm")]})
        self.assertEqual(self._load_only(path), "envelope_firm")

    def test_bare_list_still_loads(self):
        """V1 archives are a bare list. Reading the historical record must not
        require rewriting it."""
        path = self._write([_genome("list_firm")])
        self.assertEqual(self._load_only(path), "list_firm")

    def test_index_selects_within_the_envelope(self):
        path = self._write({"population": [_genome("a"), _genome("b"),
                                           _genome("c")]})
        self.assertEqual(self._load_only(path, index=2), "c")

    def test_out_of_range_is_measured_against_the_population(self):
        """The original defect: a five-key envelope made index 3 look valid.
        The bound must come from the genome list, not the container."""
        path = self._write({"generation": 1, "name": "n", "objective": "",
                            "benchmark_task": "",
                            "population": [_genome("only")]})
        with self.assertRaises(IndexError):
            self._load_only(path, index=3)

    def test_object_without_population_is_refused(self):
        path = self._write({"generation": 1, "firms": [_genome()]})
        with self.assertRaises(ValueError):
            self._load_only(path)

    def test_population_of_wrong_type_is_refused(self):
        path = self._write({"population": {"0": _genome()}})
        with self.assertRaises(ValueError):
            self._load_only(path)

    def test_scalar_file_is_refused(self):
        path = self._write("not a population")
        with self.assertRaises(ValueError):
            self._load_only(path)


if __name__ == "__main__":
    unittest.main()
