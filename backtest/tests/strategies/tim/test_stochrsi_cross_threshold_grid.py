from __future__ import annotations

import unittest

from scripts.analyze_stochrsi_cross_threshold_grid import connected_components
from scripts.run_stochrsi_cross_threshold_grid import case_id, threshold_values


class GridDefinitionTest(unittest.TestCase):
    def test_frozen_cartesian_grid_has_1681_unique_cases(self) -> None:
        buys = threshold_values(0.0, 0.4, 0.01)
        sells = threshold_values(0.6, 1.0, 0.01)
        cases = {case_id(buy, sell) for buy in buys for sell in sells}
        self.assertEqual(len(buys), 41)
        self.assertEqual(len(sells), 41)
        self.assertEqual(len(cases), 1681)
        self.assertIn("B000_S060", cases)
        self.assertIn("B020_S080", cases)
        self.assertIn("B040_S100", cases)

    def test_eight_neighbor_components_connect_diagonals(self) -> None:
        components = connected_components({(20, 80), (21, 81), (30, 90)})
        self.assertEqual([len(component) for component in components], [2, 1])


if __name__ == "__main__":
    unittest.main()
