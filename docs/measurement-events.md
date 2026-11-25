# Measurements and documented events

Run the offline example from an installed wheel. Its two cohorts contain invented people and use an illustrative Hb threshold of 10 g/dL.

```sh
lab-bench run --task synthetic-events --out synthetic-events.json
```

At day 90 the training cohort has 20 first qualifying measurements and 20 people with later event-free assessments. The estimated Kaplan–Meier risk is 0.5. The separate four-person evaluation cohort supplies two positive and two negative day-90 labels. Constant risk 0.5 gives a conditional Brier score of 0.25. A request for day 1,095 yields a null training estimate. The CGM example supplies 576 of 8,640 five-minute slots. Its 30-day GMI is unavailable.

## Measurement identity and clocks

`records.Measurement` identifies a person by the original source and a string identifier. An observation ID and optional panel-component ID identify one logical result. Repeat specimens require distinct logical IDs. A revision uses the same logical ID and a positive, increasing integer version. Keep specimen and method when available. Capture channel and measurement kind have separate fields. OCR may capture a measured result. An unspecified or incompatible measurement kind produces a counted exclusion.

`records.as_of` returns accepted points and exclusion counts for one person, concept and cutoff. It first selects versions issued through the cutoff, then takes the highest visible revision per logical result. This order permits a correction to change a value, code or effective time, and a withdrawal to remove a previously eligible result. Identical copies collapse. Conflicting copies of the newest revision exclude the logical result. A version without a known issue time can make an older value ambiguous. Collection-time proxy mode applies only to original final results and flags each affected point. A correction requires its own issue time.

`parse_timestamp` accepts offset-bearing ISO instants and naive local times with an explicit IANA zone. A repeated or missing wall-clock hour requires an explicit offset. A complete date needs `precision="day"` and a zone. It maps to the start of that source's study day, which makes it suitable for a declared date-level protocol. A prospective intraday analysis requires actual instant-level availability. A date-only issue and an unknown issuance must remain distinguishable in the source adapter. Preserve nominal visits separately in `nominal_day`. Use `cbc_daily_medians` only when a CBC protocol calls for a local-day median across distinct accepted specimens.

The supported LOINC concepts are whole-blood Hb `718-7`, measured HbA1c `4548-4` and calculated GMI `97506-0`. Raw keys should retain leading zeroes through parsing. Decimal commas, inequalities, uncertainty symbols, nonfinite values, unknown units and ambiguous revisions have explicit rejection paths. An adapter may supply a fixed unit or standard from its versioned source dictionary. For standardized HbA1c, NGSP percent is `0.09148 × IFCC mmol/mol + 2.152`. Absolute results must be nonnegative in their original units. A difference may be signed and uses only the slope. Hb in g/L is divided by ten to obtain g/dL. [NGSP master equation](https://ngsp.org/ifccngsp.asp) · [Measured HbA1c](https://loinc.org/4548-4/) · [GMI](https://loinc.org/97506-0/)

```python
from datetime import datetime, timedelta, timezone
from medextract_lab_bench.records import HB, Measurement, as_of

index = datetime(2024, 1, 1, tzinfo=timezone.utc)
baseline = Measurement("invented", "001", "draw0", HB, 140, "g/L", index, index,
                       kind="measured", specimen="venous blood")
points = as_of([baseline], source="invented", person="001", concept=HB, cutoff=index).points
assert points[0].value == 14
```

## Uniform-cadence CGM

`gmi_window` needs a source, person, start, end, as-of clock, known cadence and coverage threshold. The window spans a positive whole number of slots and uses its start as the grid origin. Endpoints are half-open. The source adapter declares the cadence from the acquisition protocol. A per-reading declaration that disagrees with it gives an unsupported-cadence status. Irregular devices need a separately specified resampling and support model.

Only positive, finite glucose in mg/dL issued at or after sampling and by the window cutoff contributes. Each slot gets equal weight. Identical values at one instant count once. Distinct valid readings at different instants in a slot are averaged. Conflicting values at the same instant exclude that slot. The diagnostics retain counts of duplicate, conflicting and invalid readings. Coverage uses the integer ceiling of the minimum fraction times the expected slots. Largest gap measures consecutive empty nominal slots, including both edges. Observed span measures actual time between the first and last contributing readings. Saved data start, data end and window end can therefore differ.

At the window end, an accepted estimate uses `3.31 + 0.02392 × mean glucose in mg/dL`. The issue clock freezes eligible readings at that end. Early runs cannot issue the result. Late uploads require a separately versioned retrospective policy. `GMIWindow.as_measurement()` emits a calculated record with its own LOINC code. A further assessment extends follow-up only if that window also qualifies. [Bergenstal and colleagues, GMI equation](https://doi.org/10.2337/dc18-1581)

## Risk entry and follow-up

`events.build_case` takes a `CaseKey`, an `EventRule` in canonical units and a later administrative analysis time. The default entry rule needs a nonqualifying measurement at the index and excludes a documented qualifying result anywhere in the available history. Equality can be included or excluded using `le`, `lt`, `ge` or `gt`. Qualifying and nonqualifying results that share the same effective instant make the state ambiguous. Same-time measurements that agree on threshold status are usable. The `incident_first_ever=False` option permits a previous qualifying result when a separately named protocol specifies recurrence. The anchor must still be nonqualifying.

A future result contributes only when its effective time follows the index and its issue time is visible by the analysis cutoff. Follow-up ends at the first documented qualifying future result or at the last evaluable assessment. With a known anchor and no subsequent result, observed event-free duration is zero. The history view stays frozen at the index. The response retains the last nonqualifying and first qualifying effective times when observed. Intermittent measurements can miss transient episodes. This endpoint measures documentation under the source observation schedule. Its observations do not establish latent biological onset or durable remission.

A known competing event on or before the first measured endpoint produces an unavailable outcome and retains its cause and time. A competing-risk estimator requires a separate protocol. A conflicting assessment encountered before the first qualifying result makes the outcome ambiguous. Exclusion counts and event-case statuses expose both paths.

```python
from medextract_lab_bench.events import CaseKey, EventRule, build_case, horizon_status

day60 = index + timedelta(days=60)
future = Measurement("invented", "001", "draw1", HB, 99, "g/L", day60, day60, kind="measured")
case = build_case([baseline, future], CaseKey("invented", "001", HB, index),
                  EventRule("le", 10), analysis_time=day60)
assert case.outcome.duration == 60
assert horizon_status(case.outcome, 30) == 0
assert horizon_status(case.outcome, 90) == 1
```

## Estimation and scoring

- `interval_support` counts people or landmarks entering each interval, known interval outcomes, events and incomplete censorings. For an interval (0, 180], a day-30 right-censor supplies no negative. A day-180 event supplies a positive. A day-180 event-free assessment supplies a negative. The intercept-only empirical hazard estimates events divided by completed labels, including one-class intervals. A zero-support interval blocks that and later cumulative risks. Mid-interval omissions are a coarse approximation. The aggregate scorecard reports both training and evaluation support.
- `km_risk` constructs risk sets using the observed event or censor duration. A failure acts before a censor at the same instant. Risk beyond the last observed duration is unavailable. `pseudo_values` builds one full curve and one leave-one-person-out curve per person, evaluating all requested horizons on each curve. A leave-one-out curve uses the conventional right-continuous KM step function at full-cohort supported horizons. Leave all landmarks of the same source-scoped person out together by passing `people`. Jackknife values can leave the unit interval. `fit_pseudo_risk` fits an intercept only and flags an invalid fitted probability. Probability regression on clinical features requires its own fit and evaluation.
- `event_scorecard` requires disjoint training and evaluation people, unique landmark keys and a common endpoint definition. Score matching uses source, person, concept, clock and index. At each horizon an event by that day is positive. A later first event or event-free observation through that day is negative. Earlier right-censoring is unknown. Brier error and reliability bins use known labels with a supported forecast. Return counts of all three statuses and forecast/evaluation denominators. Population-wide performance under informative loss to follow-up requires a separately justified censoring model.
- `concordance` compares an earlier event with a later record or a same-time censor. Two simultaneous failures are incomparable. Tied scores on comparable pairs contribute one half. An empty set of comparable pairs gives a null estimate and reason. `paired_concordance_delta` resamples source-scoped people with their repeated landmarks, uses paired scores and reports undefined bootstrap draws. Its pair enumeration and KM jackknifing suit small research cohorts.
- `venn_abers` pools equal calibration scores before weighted isotonic fitting. `conformal_radius` uses the finite-sample rank ceil((n+1)(1−alpha)). Ranks above n have no finite radius. `split_prediction_intervals` takes disjoint calibration and evaluation people, calibrating on one maximum absolute residual per person with an evaluable target. It reports the radius, width, observed targets, forecasts, issued bands and empirical coverage among evaluated landmarks. `observed_event_intervals` uses documented event times and explicitly labels its coverage as conditional. Exchangeability and the observation mechanism must be justified for any interpretation beyond this summary.

The public demonstration fits two intercept-only event baselines and evaluates implementation behavior on invented observations. There are no external event-model performance estimates, FHIR adapter or learned clinical predictors in this experiment.

### Reported decimal values at an interval boundary

Version 0.1.1 computes absolute residuals from the decimal representations of the reported target and forecast, orders calibration scores using that representation and includes equality at the endpoints. This keeps a 0.4 change from 5.2 to 5.6 equal to a 0.4 change from 6.0 to 6.4. The mean interval score adds a distance penalty of 2/alpha for each unit beyond the interval to its width, then averages over observed evaluation pairs. The change affects individual prediction-interval diagnostics. Historical replay and paired point-forecast errors retain their original numerical rules.

Version 0.1.2 also averages the two central reported decimal hemoglobin values when the corrected hospital adapter collapses an even number of same-day draws. For example, 10.3 and 10.9 give 10.6 exactly as a reported quantity. The historical replay preserves its original median arithmetic.
