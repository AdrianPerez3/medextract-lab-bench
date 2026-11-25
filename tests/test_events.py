from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import product
import json
import pytest

from medextract_lab_bench.events import (
    CaseKey,
    EventRule,
    FollowUp,
    build_case,
    horizon_status,
    interval_support,
    fit_interval_risk,
    km_risk,
    pseudo_values,
    concordance,
    conditional_score,
    align_cases,
    require_disjoint_people,
    synthetic_checks,
)
from medextract_lab_bench.records import HB, Measurement

T = datetime(2024, 1, 1, tzinfo=timezone.utc)
KEY = CaseKey("demo", "001", HB, T)
RULE = EventRule("le", 10)


def lab(day, value, **kwargs):
    stamp = T + timedelta(days=day)
    d = dict(
        source="demo",
        person="001",
        observation=f"d{day}",
        kind="measured",
        concept=HB,
        effective=stamp,
        available=stamp,
        value=value,
        unit="g/dL",
    )
    return Measurement(**(d | kwargs))


def test_incident_index_leakage_and_label_availability():
    rows = [lab(-10, 12), lab(0, 11), lab(5, 12), lab(10, 10), lab(20, 13)]
    c = build_case(rows, KEY, RULE, analysis_time=T + timedelta(days=30))
    assert (c.status, c.outcome, c.last_nonqualifying, c.first_qualifying) == (
        "eligible",
        FollowUp(10, True, 20),
        T + timedelta(days=5),
        T + timedelta(days=10),
    )
    b = replace(
        rows[1],
        value=4,
        status="corrected",
        revision=2,
        available=T + timedelta(days=2),
    )
    for changed in [
        [*rows, b, lab(1, 1, person="other")],
        rows[::-1],
        [*rows[:3], lab(15, 4)],
    ]:
        assert (
            build_case(changed, KEY, RULE, analysis_time=T + timedelta(days=30)).history
            == c.history
        )
    assert build_case(
        rows[:3], KEY, RULE, analysis_time=T + timedelta(days=30)
    ).outcome == FollowUp(5, False, 5)
    assert build_case(
        rows[:2], KEY, RULE, analysis_time=T + timedelta(days=30)
    ).outcome == FollowUp(0, False, 0)
    assert build_case(rows, KEY, RULE, analysis_time=T).outcome == FollowUp(0, False, 0)
    assert (
        build_case([lab(-1, 10), lab(0, 14)], KEY, RULE, analysis_time=T).status
        == "prior_qualifying"
    )
    assert build_case([lab(0, 10)], KEY, RULE, analysis_time=T).status == "prevalent"
    assert (
        build_case([lab(-1, 14)], KEY, RULE, analysis_time=T).status
        == "missing_anchor_assessment"
    )
    assert (
        build_case(
            [lab(0, 12), lab(0, 9, observation="different")], KEY, RULE, analysis_time=T
        ).status
        == "ambiguous_anchor"
    )
    assert (
        build_case(
            [lab(0, 12), lab(4, 9), lab(4, 12, observation="other"), lab(10, 8)],
            KEY,
            RULE,
            analysis_time=T + timedelta(days=10),
        ).status
        == "ambiguous_future_assessment"
    )


def test_horizons_and_interval_support():
    rows = [
        FollowUp(d, e)
        for d, e in [
            (0, False),
            (30, False),
            (180, False),
            (200, False),
            (180, True),
            (181, True),
        ]
    ]
    assert [horizon_status(r, 180) for r in rows] == [None, None, 0, 0, 1, 0]
    s, t = interval_support(rows, [180, 365])
    assert (s.entering, s.known, s.events, s.pre_end_censors) == (6, 4, 1, 2)
    assert (t.entering, t.known, t.events, t.pre_end_censors) == (2, 1, 1, 1)
    f = fit_interval_risk([FollowUp(730, False)], [365, 730, 1095])
    assert [p.value for p in f] == [0, 0, None]
    assert f[-1].reason == "no_training_support"
    assert fit_interval_risk([], [180, 365])[-1].value is None
    assert fit_interval_risk([FollowUp(20, True)], [30, 60])[-1].value is None
    assert horizon_status(FollowUp(181, True), 180) == 0
    for bad in [
        (0, True),
        (-1, False),
        (float("nan"), False),
        (20, 1),
        (20, True, 10),
        (20, False, 30),
    ]:
        with pytest.raises(ValueError):
            FollowUp(*bad)


def test_km_pseudo_ties_and_direct_oracle():
    outcomes = [FollowUp(60, True)] * 20 + [FollowUp(200, False)] * 20
    assert km_risk(outcomes, [90, 201]) == pytest.approx([0.5, None])
    pm = pseudo_values(outcomes, [60, 90])
    assert pm[0] == pytest.approx([1, 1])
    assert pm[-1] == pytest.approx([0, 0])
    for bits in product((False, True), repeat=4):
        cohort = [FollowUp(t, b) for t, b in zip((10, 10, 20, 30), bits)]

        def ref(rows, h):
            s = 1
            for t in sorted({r.duration for r in rows if r.event and r.duration <= h}):
                s *= 1 - sum(r.duration == t and r.event for r in rows) / sum(
                    r.duration >= t for r in rows
                )
            return 1 - s

        a, b = 8, 20
        matrix = pseudo_values(cohort, [a, b])
        for i in range(4):
            assert matrix[i] == pytest.approx(
                [
                    4 * ref(cohort, h) - 3 * ref(cohort[:i] + cohort[i + 1 :], h)
                    for h in (a, b)
                ]
            )
    assert km_risk([FollowUp(10, False), FollowUp(10, True)], [10]) == [0.5]
    assert km_risk([FollowUp(0, False)], [0, 1]) == [0, None]


def test_conditional_metrics_comparability_and_join_guards():
    rows = [FollowUp(2, True), FollowUp(2, False), FollowUp(1, False)]
    assert concordance(rows, [0.5, 0.5, 0.5]).value == 0.5
    assert concordance(rows, [0.6, 0.5, 0.1]).value == 1
    assert concordance(rows[1:], [0.2, 0.3]).reason == "no_comparable_pairs"
    s = conditional_score(rows, [0.8, 0.2, 1], 2)
    assert (s["positive"], s["negative"], s["unknown"], s["evaluated"]) == (1, 1, 1, 2)
    assert s["conditional_brier"] == pytest.approx(0.04)
    assert conditional_score(rows[2:], [0.3], 2)["conditional_brier"] is None
    later = replace(KEY, index=T + timedelta(days=1))
    with pytest.raises(ValueError):
        require_disjoint_people([KEY], [later])
    require_disjoint_people([KEY], [replace(KEY, source="other")])
    align_cases([KEY, later], [KEY, later])
    for right in [[later, KEY], [KEY, KEY], [KEY]]:
        with pytest.raises(ValueError):
            align_cases([KEY, later], right)
    json.dumps(synthetic_checks(), allow_nan=False)


def test_group_jackknife_model_and_no_extrapolation():
    from medextract_lab_bench.events import fit_pseudo_risk, median_horizon
    from medextract_lab_bench.calibration import Estimate

    rows = [FollowUp(10, True), FollowUp(10, True), FollowUp(20, False)]
    owners = ["a", "a", "b"]
    matrix = pseudo_values(rows, [10], people=owners)
    assert len(matrix) == 2 and matrix[0][0] == pytest.approx(4 / 3)
    assert matrix[1][0] == pytest.approx(1 / 3)
    assert fit_pseudo_risk(rows, [10], people=owners)[0].value == pytest.approx(5 / 6)
    assert (
        fit_pseudo_risk(rows, [10], people=["a"] * 3)[0].reason
        == "insufficient_jackknife_people"
    )
    assert (
        median_horizon([10, 20], [Estimate(0.4), Estimate(0.4)]).reason
        == "median_not_reached"
    )
    assert (
        median_horizon([10, 20], [Estimate(0.2), Estimate(None)]).reason
        == "unsupported_horizon"
    )


def test_conditioned_time_bands_bootstrap_and_task_matching():
    from medextract_lab_bench.events import (
        CompetingEvent,
        event_scorecard,
        observed_event_intervals,
        paired_concordance_delta,
    )

    def make(key, value):
        rows = [lab(0, 12, person=key.person), lab(10, value, person=key.person)]
        return build_case(rows, key, RULE, analysis_time=T + timedelta(days=20))

    cal = [make(replace(KEY, person=str(i)), 9) for i in range(9)]
    test = [
        make(replace(KEY, person="event"), 10),
        make(replace(KEY, person="censor"), 12),
    ]
    s = observed_event_intervals(
        cal, {c.key: 8 for c in cal}, test, {c.key: 10 for c in test}, 0.1
    )
    assert (
        s["targets_observed"],
        s["evaluated"],
        s["empirical_coverage"],
        s["radius"],
    ) == (1, 1, 1, 2)
    first, second = (
        {test[0].key: 0.8, test[1].key: 0.2},
        {test[0].key: 0.4, test[1].key: 0.4},
    )
    b = paired_concordance_delta(test, first, second, draws=100)
    assert b == paired_concordance_delta(test[::-1], first, second, draws=100)
    assert b["delta_first_minus_second"] == 0.5 and b["ci95"] == [0.5, 0.5]
    assert 0 < b["undefined_draws"] < 100
    with pytest.raises(ValueError):
        event_scorecard(cal, [replace(test[0], rule=EventRule("gt", 8))], [10])
    c = build_case(
        [lab(0, 12), lab(10, 8)],
        KEY,
        RULE,
        analysis_time=T + timedelta(days=11),
        competing=CompetingEvent("invented competing event", T + timedelta(days=8)),
    )
    assert c.status == "competing_endpoint_unavailable" and c.competing is not None


def test_pseudo_curve_builds_scale_with_people_across_horizons(monkeypatch):
    from medextract_lab_bench import events

    original = events._km_curve
    sizes = []

    def counted(rows):
        sizes.append(len(rows))
        return original(rows)

    monkeypatch.setattr(events, "_km_curve", counted)
    cohort = [FollowUp(12, True), FollowUp(14, False), FollowUp(7, False)]
    pseudo_values(cohort, [1, 4, 9, 12])
    assert sizes == [3, 2, 2, 2]


def test_documented_direction_equality_day_shift_and_late_availability():
    for comparison, prevalent, qualifying, at_risk in [
        ("ge", 10, 10, 8),
        ("gt", 11, 11, 10),
        ("le", 10, 10, 11),
        ("lt", 9, 9, 10),
    ]:
        rule = EventRule(comparison, 10)
        for shift in [-21, 0, 100]:
            index = T + timedelta(days=shift)
            key = replace(KEY, index=index)
            baseline = lab(shift, at_risk)
            future = lab(shift + 1, qualifying, available=index + timedelta(days=2))
            assert build_case(
                [baseline, future], key, rule, analysis_time=index + timedelta(days=1)
            ).outcome == FollowUp(0, False, 0)
            assert build_case(
                [baseline, future], key, rule, analysis_time=index + timedelta(days=2)
            ).outcome == FollowUp(1, True, 1)
            assert (
                build_case(
                    [lab(shift, prevalent), future],
                    key,
                    rule,
                    analysis_time=index + timedelta(days=3),
                ).status
                == "prevalent"
            )


def test_gmi_followup_ends_at_last_evaluable_window():
    from medextract_lab_bench.records import GMI, CGMReading, gmi_window

    end = T + timedelta(hours=1)
    window = gmi_window(
        [CGMReading("demo", "001", T, 100, "mg/dL", T)],
        source="demo",
        person="001",
        start=T,
        end=end,
        as_of_time=end,
        cadence=timedelta(hours=1),
    )
    key = replace(KEY, concept=GMI, index=end)
    case = build_case(
        [window.as_measurement()],
        key,
        EventRule("gt", 8),
        analysis_time=end + timedelta(days=730),
    )
    assert case.outcome == FollowUp(0, False, 0)
    assert horizon_status(case.outcome, 730) is None


def test_negative_source_result_cannot_create_an_incident_event():
    case = build_case(
        [lab(0, 12), lab(2, -4)], KEY, RULE, analysis_time=T + timedelta(days=3)
    )
    assert case.outcome == FollowUp(0, False, 0)
