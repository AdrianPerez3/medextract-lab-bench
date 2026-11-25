# MedExtract Lab Bench — forecasting hemoglobin and HbA1c from prior lab results

MedExtract Lab Bench is an offline Python benchmark for a simple question. Given four past blood tests, can a forecasting method predict a later measured hemoglobin or glycated hemoglobin (HbA1c) result more accurately than carrying the last value forward?

In the synthetic example, one person's hemoglobin falls from 16 to 14 to 12 to 10 grams per deciliter (g/dL) over four days. The **last-value forecast** for 90 days later is 10 g/dL. The recorded result is 11 g/dL, so the absolute error is 1 g/dL. The benchmark scores each method against the same measurements and counts how many people had a follow-up test in time to score a forecast.

## Run a first forecast

From the source directory, use Python 3.11 or newer. These shell commands install the project locally and run an example with invented measurements. Once installed, both demos work offline.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
lab-bench doctor
lab-bench run --task synthetic-demo --out numeric-demo.json
python - <<'PY'
import json

with open('numeric-demo.json') as report:
    score = json.load(report)
print(score['paired_people'], 'of', score['eligible_people'], 'people had a follow-up result')
print('Last-value mean absolute error:', score['metrics']['last']['mae'], score['unit'])
PY
```

Expect **3 of 4 people** to have a follow-up result and a last-value mean absolute error of **0.5 g/dL**. Choose a fresh output filename each time. To explore measured events, `lab-bench run --task synthetic-events --out event-demo.json` shows how incomplete follow-up and gaps in sensor readings affect what can be estimated. [Measurements and documented events](docs/measurement-events.md)

## What happened on two public datasets?

Each row reports **mean absolute error** (MAE), the average distance between the forecast and the recorded result. Lower is better. The damped trend method uses the median slope between past measurements and reduces the projected change when the history is short relative to the forecast horizon.

| Development task | People with a target / eligible | Last-value MAE | Damped trend MAE |
| --- | ---: | ---: | ---: |
| HbA1c at 12 months after bariatric surgery, using four scheduled visits through month 9 | 170 / 175 (97.1%) | 0.266 percentage points | 0.324 percentage points |
| Hemoglobin at 90 days after the fourth distinct hospital draw day, using the nearest result in days 76–104 | 4,737 / 39,687 (11.9%) | 1.278 g/dL | 1.895 g/dL |

Carrying the last result forward beat the damped trend on both development tasks. A historical comparison also shows the risk of extrapolating from a short history. The exponential smoother with drift produced a negative hemoglobin forecast for 649 of the 4,737 people with a recorded target.

The hospital errors describe the 11.9% who had a result in the target window. The two studies also use different clocks. The HbA1c data give scheduled visits, and the hospital data give shifted draw dates. Exact result-availability times are unknown. [Data sources, selection rules and timing](docs/data.md)

## Does the hemoglobin forecast work at a second hospital?

A separate model learned an adjustment to the latest hemoglobin result from 3,273 people with follow-up at Klinikum Lippe in Germany. The package includes the frozen fit and a replay on VitalDB from Seoul National University Hospital. Of 226 surgical patients with four prior draws and a fully observable follow-up window, 55 had a measured outcome. On those 55, mean absolute error was **1.433 g/dL** for the model and **1.686 g/dL** for the latest measured value. The paired 95% interval for the reduction was 0.069–0.439 g/dL. The model also overpredicted by **0.542 g/dL** on average, and its median absolute error rose from 1.400 to 1.496 g/dL. Follow-up was selective and clinical benefit remains unknown. [Reproduce the Seoul results](docs/vitaldb-results.md)

The Boston workflow counts eligible patients before scoring the same frozen model in an authorized MIMIC-IV 3.1 environment. To try the full calculation on invented data, install the optional dependency with `python -m pip install '.[boston]'`, then run `lab-bench run --task synthetic-boston`. The public MIMIC-IV demo supports a separate counts-only rehearsal. [Boston commands, cohort and reporting rules](docs/boston-validation.md)

## Read a numeric scorecard

- `eligible_people` counts people with the required history. `paired_people` counts those with an observed target. `eligible_without_target` records the rest.
- `metrics.last.mae` gives the baseline error. Under `metrics.damped_span_slope_v1`, a positive `paired_minus_last_mae` means the trend method had larger errors on cases where all methods returned a finite forecast. `paired_minus_last_ci95` gives the 95% confidence interval for that average difference.
- Under `methods`, each method counts invalid forecasts, abstentions and fallbacks among eligible people and among people with an observed target. Overall error includes finite negative forecasts. `accepted_only` scores valid forecasts produced without fallback.
- `prediction_intervals` reports how often a range around the last-value forecast contained an individual result on held-out people, and how wide those ranges were. [Scorecard schema](src/medextract_lab_bench/protocols/scorecard.schema.json) · [Method definitions](src/medextract_lab_bench/protocols/methods-v1.json)

## Reproduce a public-data result

Obtain the original file from the provider and set `INPUT` to its local path. For the bariatric task, use the wide file in the Dataverse tabular download format. The following commands check its fingerprint, reproduce and verify the historical report, then write the current numeric scorecard. [Files, formats and licenses](docs/data.md)

```sh
TASK=bariatric_hba1c_9m_12m_v0
INPUT=/path/to/downloaded-wide-file.tab
lab-bench validate --task "$TASK" --input "$INPUT"
lab-bench replay --task "$TASK" --input "$INPUT" --out historical.json
lab-bench verify --task "$TASK" --scorecard historical.json
lab-bench run --task "$TASK" --input "$INPUT" --out scorecard.json
```

For the hospital task, use `TASK=lippe_hemoglobin_day90_v0`, point `INPUT` to `KL_dataset.xlsx` and choose new output paths. The hospital reader uses temporary disk space to sort the workbook. The reproduction guide covers pinned dependencies and the optional follow-up count check on the public Medical Information Mart for Intensive Care (MIMIC-IV) demo. [Reproduction and external evaluation](docs/reproduction.md)

## Data, citation and contributions

- Jo Ann Galvan published the Malaysian bariatric surgery dataset in Harvard Dataverse under CC0 1.0. [Bariatric surgery dataset](https://doi.org/10.7910/DVN/T9OBE3)
- Barış Gün Sürmeli and colleagues published the Klinikum Lippe *Pseudonymized Complete Blood Count Test Data* on Zenodo under CC BY 4.0. [Blood count dataset](https://doi.org/10.5281/zenodo.15674541)
- Hyung-Chul Lee and Chul-Woo Jung published VitalDB v1.0.0 on PhysioNet under CC BY 4.0. The Seoul scorecard uses daily medians and aggregate forecast errors derived from this release. [VitalDB dataset](https://doi.org/10.13026/czw8-9p62)
- Cite the software and the dataset used. The code is MIT-licensed. [Software citation](CITATION.cff) · [License](LICENSE)
- To run the tests or propose a change, see the [contributing guide](CONTRIBUTING.md).
