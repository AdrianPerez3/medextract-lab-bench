# Boston hospital validation of the frozen hemoglobin forecast

This workflow evaluates how well four routine hemoglobin measurements predict a later measured value near day 90 at Beth Israel Deaconess Medical Center. Run qualification first to find out how many people have an observable target. The full Boston forecast comparison awaits execution by a credentialed analyst. Inputs and temporary working tables stay on the analyst's local filesystem. [MIMIC-IV v3.1](https://physionet.org/content/mimiciv/3.1/) · [PhysioNet access agreement](https://physionet.org/content/mimiciv/view-dua/3.1/)

## Try it with invented data

From the source directory, install the Boston extra to enable the DuckDB adapter. The invented example includes one short history, one history longer than a year and one person without a measured follow-up. It runs the same extraction and scoring code as the full comparison.

```sh
python -m pip install '.[boston]'
lab-bench run --task synthetic-boston --out invented-score.json
```

The example has three eligible people and two paired outcomes. Repeating the most recent value gives a mean absolute error of 1.5 g/dL.

## Rehearse the counts on the public demo

Use the original gzip files under the demo's `hosp/` directory. The program verifies the public release hashes for the lab event table and dictionary. Set `MIMIC_DEMO_22` to the release root and `APPROVED_SCRATCH` to an existing private working directory.

```sh
lab-bench qualify-boston-demo --root "$MIMIC_DEMO_22" --scratch "$APPROVED_SCRATCH" --out demo-counts.json
```

In the public demo, 90 people reached an eligible origin. Twelve had an in-window target, 88 had both index-day MCV and percent RDW linked to an available hemoglobin specimen, and 37 had any later laboratory contact on or after day 104. All 12 with a target had both markers. The demo is a previously studied subset of the full cohort and supports counts only. [MIMIC-IV demo v2.2](https://physionet.org/content/mimic-iv-demo/2.2/)

## Run the frozen comparison in an authorized environment

Use the compressed v3.1 originals for `hosp/labevents.csv.gz`, `hosp/d_labitems.csv.gz` and `hosp/patients.csv.gz`. Supply the official SHA-256 manifest obtained with those data. The source of this manifest establishes the provenance of its hashes. The adapter also checks the published v3.1 totals of 1,650 dictionary records, 364,627 patients and 158,374,764 lab events, counting all rows before cohort exclusions. These checks distinguish common wrong releases; the hashes must still come from the original provider manifest. [MIT-LCP release validation counts](https://github.com/MIT-LCP/mimic-code/blob/e3e0310d9ad49dfba7947f630a55e4c7fdfa68f2/mimic-iv/buildmimic/postgres/validate.sql) Also obtain the original public v2.2 `hosp/patients.csv.gz`. The program checks its pinned digest and matches all 100 demo subject keys with gender, anchor age, anchor year and anchor-year group in the v3.1 roster before excluding them. Resolve a discrepancy with the data custodian before using a different identity mapping. [Official demo files](https://physionet.org/content/mimic-iv-demo/2.2/#files-panel)

```sh
lab-bench qualify-boston --root "$MIMIC_31" --manifest "$MIMIC_31/SHA256SUMS.txt" \
  --demo-patients "$MIMIC_DEMO_22/hosp/patients.csv.gz" --scratch "$APPROVED_SCRATCH" --out boston-counts.json
lab-bench score-boston --root "$MIMIC_31" --manifest "$MIMIC_31/SHA256SUMS.txt" \
  --demo-patients "$MIMIC_DEMO_22/hosp/patients.csv.gz" --scratch "$APPROVED_SCRATCH" --out boston-score.json
```

Choose a new filename for each run. DuckDB scans the lab file for last contact and eligible assays, with a 1 GB database working-memory target and private temporary disk space for sorting. Qualification writes aggregate counts. Scoring applies the frozen model and both published baselines once to the same measured people. Clinical context, disclosure rules and permission for subsequent export of aggregates remain site-specific.

## Cohort and measurement rules

- Include all hospital-module laboratory events, including events without an admission ID. Use exact numeric routine blood hematology item 51222. Require matching finite, nonnegative text and numeric values. Convert g/L to g/dL before reducing draws. Require a collection and storage timestamp with storage at or after collection. MIMIC documents chart time as the timestamp of the measurement, usually specimen acquisition, and store time as when the measurement became available. [Laboratory fields](https://mimic.mit.edu/docs/iv/modules/hosp/labevents.html)
- Anchor each person once, at the end of their fourth distinct chart date with at least one eligible Hb result stored on that same date. The model uses those four dates. Measurements on an earlier qualifying date can contribute once they have been stored by the end of the fourth date. A late result alone cannot make a date qualify.
- For each selected collection date, repeated identical numeric results from the same specimen and collection time count once. If multiple distinct results remain at that specimen and time, take their median. Then take the median across draws that day. A missing specimen key falls back to the laboratory event key. Raw Hb uses decimal arithmetic with 12 fractional places before conversion to the model's numerical input. The released table does not identify a complete correction lineage.
- Select the eventual daily Hb nearest day 90 within days 76 through 104, inclusive, using the earlier date for equal distances. Keep every eligible origin in the denominator. An absence of a measured target leaves forecast error unknown. Any lab at or after day 104 documents later contact only. It cannot establish continuous coverage, survival or attendance at another provider.
- Count MCV (51250, fL) and RDW-CV (51277, %) as available when they share an index-day specimen with an available Hb. Both-marker availability requires all three on one specimen. These markers describe cohort characteristics. The frozen point model uses Hb values and history timing only.
- The German development cohort used complete CBC panels and a documented prior-transfusion selection. The Boston population includes the broader set of qualifying laboratory records. Document care setting, follow-up, transfusions when available, timing limitations and differences in case mix alongside any estimate. [Development cohort](https://zenodo.org/records/15674541)

## Read the results

The primary comparison is the paired model-minus-last-value mean absolute error on the same measured patients. Freeze the comparison before scoring, retain its result for either direction of change, and report signed bias and the frequency and size of overprediction alongside mean error. About 621 measured outcomes corresponds to an illustrative ±0.05 g/dL precision under the German development dispersion. Actual Boston precision depends on its observed variation.

Check `counts.eligible`, `counts.with_target` and `counts.without_target` first. In scoring mode, `scorecard.methods` reports metrics for the frozen model, last value and the history-span rule. Positive signed bias means the forecasts were high on average. Counts and fractions of overpredictions use observed outcomes. Mean overprediction when high describes the size of misses in that direction. Read median and 90th-percentile absolute error alongside the mean. The `model_minus` contrasts pair each patient's model error with the baseline error. Negative differences favor the frozen model. The 95% percentile confidence intervals use 5,000 paired patient resamples. With fewer than 30 pairs, confidence intervals are omitted.
