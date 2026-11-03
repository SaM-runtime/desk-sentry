"""The analysis must enforce the console's adoption gate, without inventing precision."""

import pytest
from tools.replay_diagnostics import select_limb_parameters


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([], None),
        ([{"removed_count": 0, "unlike_hand_count": 0, "rough_hand_ratio": None}], None),
        ([{"removed_count": 10, "unlike_hand_count": 4, "rough_hand_ratio": 0.6}], None),
        (
            [
                {
                    "removed_count": 10,
                    "unlike_hand_count": 2,
                    "rough_hand_ratio": 0.8,
                    "rough_hand_count": 8,
                },
                {
                    "removed_count": 10,
                    "unlike_hand_count": 1,
                    "rough_hand_ratio": 0.9,
                    "rough_hand_count": 9,
                },
            ],
            1,
        ),
    ],
)
def test_sweep_selection_requires_evidence_and_respects_unlike_hand_cap(rows, expected):
    selected = select_limb_parameters(rows)
    if expected is None:
        assert selected is None
    else:
        assert selected == rows[expected]
