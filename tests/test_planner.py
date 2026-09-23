import unittest

from builddiet.planner import PlanItem, size_first, solve

GB = 10**9


def item(name, size_gb, seconds, reuse=1.0):
    return PlanItem("p", "/p", name, int(size_gb * GB), seconds, reuse)


class PlannerTest(unittest.TestCase):
    def test_workstation_example(self):
        items = [item("A", 12, 20), item("B", 8, 5), item("C", 30, 3 * 3600), item("D", 4, 2)]
        plan = solve(items, 20 * GB)
        self.assertTrue(plan.feasible)
        self.assertEqual(sorted(i.path for i in plan.items), ["A", "B"])
        self.assertEqual(plan.cost, 25)

    def test_build_farm_example_beats_size_first(self):
        items = [item("A", 80, 14), item("B", 40, 8 * 3600), item("C", 25, 120), item("D", 30, 17)]
        plan = solve(items, 100 * GB)
        self.assertEqual(sorted(i.path for i in plan.items), ["A", "D"])
        self.assertGreaterEqual(plan.freed, 100 * GB)
        self.assertGreater(size_first(items, 100 * GB).cost, plan.cost)

    def test_reuse_probability_changes_the_choice(self):
        items = [item("hot", 10, 60, reuse=1.0), item("cold", 10, 600, reuse=0.01)]
        plan = solve(items, 10 * GB)
        self.assertEqual([i.path for i in plan.items], ["cold"])

    def test_infeasible(self):
        plan = solve([item("A", 1, 1)], 5 * GB)
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.freed, GB)

    def test_always_meets_target_despite_rounding(self):
        items = [item(str(n), 0.3337 + n * 0.0001, n + 1) for n in range(30)]
        for target in (1, 12345, GB, int(3.3 * GB)):
            plan = solve(items, target)
            self.assertTrue(plan.feasible)
            self.assertGreaterEqual(plan.freed, target)

    def test_matches_brute_force(self):
        import itertools
        import random

        rng = random.Random(7)
        for _ in range(40):
            items = [item(str(i), rng.uniform(0.1, 20), rng.uniform(0, 500)) for i in range(8)]
            target = int(rng.uniform(1, 40) * GB)
            best = min(
                (sum(i.cost for i in combo) for r in range(1, 9)
                 for combo in itertools.combinations(items, r)
                 if sum(i.bytes for i in combo) >= target),
                default=None,
            )
            plan = solve(items, target)
            if best is None:
                self.assertFalse(plan.feasible)
            else:
                self.assertGreaterEqual(plan.freed, target)
                # exact up to the size resolution
                self.assertLessEqual(plan.cost, best + 1e-6 + 0.02 * best)


if __name__ == "__main__":
    unittest.main()
