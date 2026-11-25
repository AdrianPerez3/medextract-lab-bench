from itertools import permutations
import pytest
from medextract_lab_bench.calibration import conformal_radius, venn_abers


def test_score_ties_with_all_label_orders():
    for labels in set(permutations([0, 0, 1, 1])):
        assert venn_abers([0.4] * 4, labels, 0.4) == pytest.approx((0.4, 0.6, 0.5))


def test_isotonic_pooling_for_order_violation():
    assert venn_abers([0, 1], [1, 0], 0.5) == pytest.approx((1 / 3, 2 / 3, 0.5))


def test_finite_sample_quantile_and_invalid_inputs():
    for n in [0, 8]:
        x = conformal_radius(range(n), 0.1)
        assert x.value is None and x.reason == "insufficient_calibration_support"
    assert conformal_radius(range(9), 0.1).value == 8
    for invalid in [0, 1, float("nan")]:
        with pytest.raises(ValueError):
            conformal_radius([1], invalid)
    with pytest.raises(ValueError):
        conformal_radius([-1], 0.5)
    with pytest.raises(ValueError):
        venn_abers([1, 2], [0], 2)


def test_split_intervals_disjoint_people_and_all_denominators():
    from medextract_lab_bench.calibration import (
        IntervalCase,
        split_prediction_intervals,
    )

    calibration = [IntervalCase("site", str(i), 10 + i, 10) for i in range(9)]
    evaluation = [
        IntervalCase("site", "a", 5, 0),
        IntervalCase("site", "b", 12, 0),
        IntervalCase("site", "c", None, 10),
        IntervalCase("site", "d", 12, None),
    ]
    s = split_prediction_intervals(calibration, evaluation, 0.1)
    assert (
        s["forecast_issued"],
        s["targets_observed"],
        s["evaluated"],
        s["intervals_issued"],
    ) == (3, 3, 2, 3)
    assert (s["radius"], s["empirical_coverage"], s["mean_width"]) == (8, 0.5, 16)
    assert (
        split_prediction_intervals(calibration[:8], evaluation, 0.1)["intervals_issued"]
        == 0
    )
    with pytest.raises(ValueError):
        split_prediction_intervals(
            calibration, [IntervalCase("site", "1", None, None)], 0.1
        )


def test_person_is_the_calibration_sampling_unit():
    from medextract_lab_bench.calibration import (
        IntervalCase,
        split_prediction_intervals,
    )

    repeated = [IntervalCase("s", "one", 0, d) for d in range(10)]
    assert split_prediction_intervals(repeated, [], 0.1)["radius"] is None
    assert split_prediction_intervals(repeated, [], 0.5)["radius"] == 9


def test_equal_reported_decimal_differences_tie_at_interval_boundary():
    from medextract_lab_bench.calibration import (
        IntervalCase,
        split_prediction_intervals,
    )

    calibration = [IntervalCase("site", "cal", 5.6, 5.2)]
    evaluation = [
        IntervalCase("site", "equal", 6.4, 6.0),
        IntervalCase("site", "outside", 6.400001, 6.0),
    ]
    result = split_prediction_intervals(calibration, evaluation, 0.5)
    assert result["radius"] == 0.4
    assert result["covered"] == 1
    assert result["mean_interval_score"] > result["mean_width"]
