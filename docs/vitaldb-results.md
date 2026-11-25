# Reproduce the Seoul hospital hemoglobin forecast

A model trained at Klinikum Lippe in Germany forecast routine blood hemoglobin 90 days after a patient's fourth measured day. On 55 surgical patients with an observed follow-up at Seoul National University Hospital, the frozen model had a mean absolute error of **1.433 g/dL**. Repeating the last measurement gave **1.686 g/dL**. On those same people, the estimated average reduction was **0.254 g/dL** (95% paired bootstrap interval 0.069–0.439). The model tended to predict high and its median absolute error increased.

| Measure, g/dL | Last measurement | Frozen model |
| --- | ---: | ---: |
| Mean absolute error | 1.686 | 1.433 |
| Median absolute error | 1.400 | 1.496 |
| 90th percentile absolute error | 3.520 | 2.816 |
| Root mean square error | 2.072 | 1.749 |
| Mean signed error (prediction minus measurement) | +0.165 | +0.542 |

The frozen model predicted above the measurement for **36 of 55 people (65.5%)**, compared with **29 of 55 (52.7%)** using the last result. Among overpredictions, the average excess was 1.508 g/dL for the model and 1.755 g/dL for the baseline. The signed errors in the table average over all 55 forecasts.

## Replay the result

The command reads five unmodified files from one directory and checks their SHA-256 fingerprints against VitalDB version 1.0.0. Download the two clinical tables, their dictionaries and the license from PhysioNet. The subsequent scoring command runs offline. [VitalDB files and license](https://physionet.org/content/vitaldb/1.0.0/#files) · [Publisher checksums](https://physionet.org/files/vitaldb/1.0.0/SHA256SUMS.txt)

```sh
VITALDB_DIR=data/vitaldb-1.0.0
mkdir -p "$VITALDB_DIR"
for name in clinical_data.csv lab_data.csv clinical_parameters.csv lab_parameters.csv LICENSE.txt
do
    curl --fail --show-error --silent --location \
        "https://physionet.org/files/vitaldb/1.0.0/$name" --output "$VITALDB_DIR/$name" || exit 1
done
lab-bench replay-seoul --root "$VITALDB_DIR" --out seoul.json
lab-bench verify --task vitaldb_hemoglobin_day90_v1 --scorecard seoul.json
```

The `primary` entry contains the prespecified complete-window analysis. Its `model_minus.last` entry gives the model's error minus the baseline error, so a negative difference favors the model. The secondary result includes the primary patients. [Seoul adapter](../src/medextract_lab_bench/adapters/vitaldb.py)

## Who the results describe

VitalDB contains 6,388 surgical cases from 6,090 people. We selected one case per person using the smallest randomly assigned case number before inspecting available blood tests. We used the routine CBC hemoglobin assay (`hb`, g/dL), expressed sample time relative to anesthesia start, and grouped samples into 24-hour bins. The median used each bin's reported decimal values. The forecast used the first four distinct bins. The target was the result closest to 90 bins after the fourth, within days 76–104 inclusive, with ties resolved toward the earlier day. [VitalDB study](https://doi.org/10.1038/s41597-022-01411-5) · [Laboratory dictionary](https://physionet.org/files/vitaldb/1.0.0/lab_parameters.csv)

| Target window relative to extract | People with four draws | People with measured follow-up |
| --- | ---: | ---: |
| Entirely inside, primary | 226 | 55 |
| Partly inside | 2,568 | 67 |
| Outside | 166 | 0 |
| All eligible people, secondary | 2,960 | 122 |

VitalDB extracted labs 90 days before and after anesthesia start. The entire target window therefore fits only for people whose fourth draw fell at least 15 bins before surgery. Of those 226 people, 171 had no measured target. Their forecast errors remain unknown. The source records relative test time, removes nonnumeric qualifiers and lacks result-availability timestamps. These measurements describe a selected surgical population and a retrospective time proxy. The published 365-day smoothing rule always repeats the last value here because every history spans less than a year.

## Attribution

The aggregate results were calculated from VitalDB v1.0.0, published by Hyung-Chul Lee and Chul-Woo Jung on PhysioNet in 2022 under CC BY 4.0. This analysis transformed measurements into daily medians and aggregate forecast errors. The model used the German data documented in the benchmark's data guide. [VitalDB dataset](https://doi.org/10.13026/czw8-9p62) · [VitalDB license](https://physionet.org/files/vitaldb/1.0.0/LICENSE.txt) · [German data and attribution](data.md)
