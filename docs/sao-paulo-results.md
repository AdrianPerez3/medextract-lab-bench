# A 90-day hemoglobin forecast in São Paulo

At Hospital Israelita Albert Einstein, a model trained in Germany reduced mean absolute error from **1.368 to 1.308 g/dL** compared with repeating the last hemoglobin result. The comparison used the same **700 people** for both methods. They represented **17.7% of the 3,944** people with four qualifying measurement days. The estimate applies to those with a measured follow-up. [Frozen protocol](sao-paulo-protocol.md) · [Aggregate scorecard](../src/medextract_lab_bench/protocols/sao-paulo-hemoglobin-results.json)

| Error in g/dL, 700 measured people | Repeat last result | Frozen model |
| --- | ---: | ---: |
| Mean absolute error | 1.368 | 1.308 |
| Median absolute error | 1.000 | 1.092 |
| 90th percentile of absolute error | 3.100 | 2.724 |
| Root mean squared error | 1.822 | 1.650 |
| Mean signed error (forecast minus measurement) | −0.277 | −0.215 |

The paired reduction in mean absolute error was **0.061 g/dL** (95% patient-bootstrap interval **0.015–0.108**). The model reduced larger misses while its median error rose. It overpredicted for 299 of 700 people (42.7%), by 1.279 g/dL on average among those overpredictions. The history-span comparator in the scorecard coincides with repeating the last result because the source covers less than one year. This is a retrospective research benchmark.

## Reproduce the comparison

From a checkout of this repository, install with Python 3.11 or newer. Obtain the original `EINSTEINAgosto.zip` from the hospital's FAPESP record using “Visualizar/Abrir” and the repository’s download form. Point the command at the unopened ZIP on your own machine. The replay checks the archive fingerprint, streams the two CSV members locally and writes an aggregate scorecard. A new output filename is required on each run. [Official hospital archive, item 98](https://repositoriodatasharingfapesp.uspdigital.usp.br/handle/item/98)

```sh
python -m pip install .
lab-bench replay-sao-paulo --archive "/path/to/EINSTEINAgosto.zip" --out sao-paulo.json
lab-bench verify --task sao_paulo_hemoglobin_day90_v1 --scorecard sao-paulo.json
```

Verification returns `matched_external_scorecard` when the reproduced fields match the bundled reference. In the JSON, `all.model_minus.last.delta_mae` is **model minus baseline**, so −0.060687 g/dL corresponds to the positive improvement above. The `complete` entry scores the subset with a full calendar window. The fit and its bootstrap procedure are shared with the Seoul evaluation. [São Paulo reader](../src/medextract_lab_bench/adapters/sao_paulo.py) · [Frozen scoring code](../src/medextract_lab_bench/hemoglobin.py)

## Who contributed a measurement?

The roster covered 79,863 people tested for COVID-19. Of those, 22,102 had at least one usable routine hemoglobin measurement and 3,944 had four distinct collection days. We used the median on each day, anchored each person on their fourth qualifying day, and chose the subsequent daily result closest to day 90 within days 76–104 inclusive. Ties selected the earlier date. The archive contains collection dates from November 2019 through August 2020.

| Time remaining in the archive after the fourth measurement | Eligible people | With a measured target |
| --- | ---: | ---: |
| At least 104 days | 1,599 | 638 |
| 76–103 days | 583 | 62 |
| Less than 76 days | 1,762 | 0 |

Among the **638** measured people with a complete calendar window, the model's MAE was **1.308** versus **1.373 g/dL** for repeating the last result. The paired improvement was 0.065 g/dL (95% interval 0.018–0.113). These 638 people are included in the main 700-person comparison. Sufficient time in the archive does not establish attendance or continuous follow-up for an individual.

The routine panel supplied 79,069 hemoglobin rows before unit and numeric checks. Two had unsupported units, and 385 lacked an accepted numeric value. The retained 78,682 rows covered 77,479 person-days. Multiple rows on 1,133 person-days contributed to daily medians. The lab table lacks result-release timestamps, specimen identifiers, correction histories and transfusion status. Performance at the moment of a clinical decision, clinical benefit, and error for people without a measured target remain unknown.

## Cite the data

This work used data obtained from the COVID-19 Data Sharing/BR, available at https://repositoriodatasharingfapesp.uspdigital.usp.br/. FAPESP (2020), *FAPESP COVID-19 Data Sharing/BR*. Hospital Israelita Albert Einstein, *Dados COVID Hospital Israelita Albert Einstein*, item 98. The repository specifies CC BY and provides its citation instructions. The public scorecard contains aggregate results. [FAPESP repository](https://repositoriodatasharingfapesp.uspdigital.usp.br/) · [Citation instructions](https://repositoriodatasharingfapesp.uspdigital.usp.br/static/images/refEN.html) · [German training data](https://zenodo.org/records/15674541)
