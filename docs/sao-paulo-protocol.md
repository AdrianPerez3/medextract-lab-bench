# São Paulo external comparison of 90-day hemoglobin forecasts

Frozen before counting eligible patients or calculating forecast error. One site and one archive were selected in advance. The archive structure, dictionary and assay labels were inspected to define the mapping.

## Source and population

Use Hospital Israelita Albert Einstein, item 98, EINSTEINAgosto.zip, SHA-256 `6fe4a880a55daf90f27e688ed21cd7ab4480c3901d5b4f52fa3a3750ae5c9d29`. Include people in the patient roster with qualifying measurements across the provider's settings. This is a COVID-tested population. The dictionary defines the pseudonymous linkage key and day/month/year collection dates. Result release times, specimen IDs, correction chains, dates of individual continuous coverage and transfusion status are unavailable in the lab file. The recorded dates support a retrospective comparison. Same-day result availability in practice and the fidelity of source deidentification cannot be independently verified from these fields.

Use the exact exam “Hemograma com Plaquetas”, analyte “Hemoglobina”, and unit “g/dL”. Accept plain finite nonnegative decimal results, allowing a decimal comma or point. Count missing or unsupported values and units. Use the median of all qualifying source rows per person and collection date. Multiple rows can represent multiple draws or duplicate records. Report their frequency. Use no physiological thresholds, imputation, refitting or calibration on this site.

## Qualification and scoring

Count all rostered people, people with at least one qualifying day, the subset with four distinct days and the subset with a measured follow-up. One origin per person, always the fourth qualifying day. The target is the daily measurement nearest day 90 among days 76 through 104 inclusive after the origin. An equal distance selects the earlier date. Report missing follow-up even when the archive ends before a full window. Report complete, partial and unobservable windows separately using the last collection date seen anywhere in the lab file as the administrative endpoint. This describes extract coverage, not patient attendance.

The primary comparison is model-minus-last-value paired mean absolute error across all measured people. Also report the subset with a complete administrative window, the history-span baseline, signed bias, and the proportion and size of overpredictions. Follow the released scorecard's 5,000 paired patient bootstrap draws at seed 20261126 in sorted patient-key order and its minimum of 30 pairs for a confidence interval. Retain the result in either direction. Treat precision and follow-up rate as part of interpretation. An estimate with few pairs is descriptive. Reuse the model and scoring code in MedExtract Lab Bench commit `70bb94adda917594c3a972b968474f2c618712d1`, fit SHA-256 `7dfc1270ecf6165910e3fe6189c17a869ee1d5cc49d1baf119dab4ea138671ac`.

Save local aggregate outputs only and verify qualification with an independent implementation. Do not publish data or results as part of this run.

## Sources

Hospital Israelita Albert Einstein, Dados COVID Hospital Israelita Albert Einstein, patient and exam dictionaries in the archive. [Hospital record](https://repositoriodatasharingfapesp.uspdigital.usp.br/handle/item/98)

FAPESP (2020). FAPESP COVID-19 Data Sharing/BR. This work used data obtained from the COVID-19 Data Sharing/BR, available at https://repositoriodatasharingfapesp.uspdigital.usp.br/.
