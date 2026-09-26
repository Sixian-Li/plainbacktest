import unittest

import numpy as np
import pandas as pd

from quantkit.surface import analyze_surface, connected_components


class SurfaceAnalysisTest(unittest.TestCase):
    def test_connected_components_uses_four_neighbors(self):
        mask = np.array(
            [
                [True, False, False],
                [False, True, True],
                [False, True, False],
            ]
        )
        self.assertEqual([len(item) for item in connected_components(mask)], [3, 1])

    def test_representative_comes_from_largest_high_plateau(self):
        records = []
        values = np.zeros((6, 6), dtype=float)
        values[2:5, 1:4] = 8.0
        values[3, 2] = 8.1
        values[0, 5] = 10.0
        for row, a_pct in enumerate(np.arange(6, dtype=float)):
            for column, b_pct in enumerate(np.arange(6, dtype=float)):
                records.append(
                    {"a_pct": a_pct, "b_pct": b_pct, "cagr_pct": values[row, column]}
                )
        result = analyze_surface(pd.DataFrame(records), top_quantile=0.75)
        representative = result["stable_representative"]
        self.assertTrue(2.0 <= representative["a_pct"] <= 4.0)
        self.assertTrue(1.0 <= representative["b_pct"] <= 3.0)
        self.assertGreater(result["largest_plateau"]["cell_count"], 1)
        self.assertFalse(result["global_best"]["in_largest_plateau"])
        self.assertTrue(result["global_best"]["locally_isolated_spike"])


if __name__ == "__main__":
    unittest.main()
