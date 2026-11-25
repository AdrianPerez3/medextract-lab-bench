# Reproducible runs and external cohorts

## Rebuild the frozen 90-day hemoglobin model

The six-feature model predicts a change from the last Hb using its level, two differences from earlier values, the variability of the four measurements, and the time spanned by the first-to-last and final two draws. Training used 3,273 German patients with measured outcomes. A deterministic patient split kept evaluation patients outside fitting and penalty selection. The included 19 numbers are aggregate feature means, scales, ridge coefficients and an intercept. All site evaluations use the same parameters.

From the source checkout or extracted source distribution, install the package, set `LIPPE_WORKBOOK` to the original workbook and run the following command.

```sh
python tools/rebuild_hemoglobin_fit.py --workbook "$LIPPE_WORKBOOK"
```

The script verifies the workbook, repeats five-fold training-only penalty selection, and checks the fitted parameters against the reference within an absolute numerical tolerance of 1e-12. Use `--require-exact` to require identical binary64 parameter bytes. Numerical library differences can affect the last bits during refitting. The regular site commands use the supplied fit directly. [Original source and license](data.md) · [Fitting code](../tools/rebuild_hemoglobin_fit.py)

- `lab-bench replay-seoul` evaluates the frozen model on the VitalDB surgical cohort. [Seoul instructions](vitaldb-results.md)
- `lab-bench replay-sao-paulo` streams the original FAPESP ZIP and reproduces the 700-person measured comparison. [São Paulo instructions and frozen protocol](sao-paulo-results.md)
- `lab-bench qualify-boston` counts eligible full-cohort patients before scoring. [Boston instructions](boston-validation.md)

## Create a methods lock

Use Python 3.11 and the hash-pinned runtime for the reference environment. Copy the source archive, the built wheel, the dependency lock and platform-appropriate dependency wheels to the evaluation environment. Keep provider data and temporary disk space in authorized locations.

```sh
python -m pip download --only-binary=:all: --require-hashes -r requirements/runtime.lock -d "$WHEELHOUSE"
python -m pip install --no-index --find-links "$WHEELHOUSE" --require-hashes -r requirements/runtime.lock
python -m pip install --no-index --no-deps "$BENCH_WHEEL"
lab-bench lock --wheel "$BENCH_WHEEL" --dependencies requirements/runtime.lock --out methods-lock.json
lab-bench verify-lock --wheel "$BENCH_WHEEL" --dependencies requirements/runtime.lock --lock methods-lock.json
lab-bench qualify-demo --wheel "$BENCH_WHEEL" --dependencies requirements/runtime.lock --lock methods-lock.json \
  --dictionary "$MIMIC_DEMO/hosp/d_labitems.csv.gz" --events "$MIMIC_DEMO/hosp/labevents.csv.gz" --out mimic-demo-counts.json
```

The local lock identifies the actual installed code and packaged methods, dependency lock, runtime versions, wheel bytes and local issue time. An independently held copy of its digest is needed to establish what preceded outcome access. For a formal external evaluation, retain the source snapshot, development receipts, independent review and an independently dated custodian-signed protocol. Build a task-specific assay mapping, chronology, population and disclosure policy before opening the data. A hash by itself cannot prove the custodian's authorization, the date, or the independence of participants.

The public demo command requires the published fingerprints for both files. It matches the hematology dictionary item for routine blood hemoglobin and counts unique draw days, available-by-day-end histories and remeasurement in days 76–104. Its results describe the selected demo. It does not produce forecast accuracies or linked records. A care setting restricted to inpatient stays or a post-transfusion exclusion requires further inputs.

For a new performance evaluation, select the primary comparator and hypothesis on development, enter versioned data terms, desired precision, unit scale, subgroup rules and error handling in the protocol, and qualify independence with the custodian. Run the first approved comparison once. Preserve its result, including a null or a losing method. Later model changes require fresh evaluation. A method that loses to persistence in development stops under this release's promotion policy. An external cohort can remain reserved for the next prospectively specified version.
