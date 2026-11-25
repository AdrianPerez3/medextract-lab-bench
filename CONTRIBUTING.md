# Contributing to MedExtract Lab Bench

A useful contribution makes a forecast easier to reproduce or a lab measurement easier to interpret. To report a problem, include the task name, Python and package versions from `lab-bench doctor`, the expected result, and a small example made entirely from invented values. Keep names, identifiers, source reports, credentials and private paths out of issues, pull requests and attachments.

## Run the checks

From a source checkout in a fresh virtual environment:

```sh
python -m pip install '.[test]' build
python -m pytest -q
python -m build
python tools/check_dist.py dist/*
```

Tests use invented records. The two historical comparisons have separate instructions for running locally with the original provider files. Store those inputs outside the checkout or in the ignored `data/` directory. Save local scorecards under `results/`. [Data sources and licenses](docs/data.md)

## Change a method or adapter

- For an input or timing fix, add an invented example that fails before the fix. Include the measurement unit and what was known on the prediction day.
- For a new method, state the population, forecast horizon and comparator. Report how many people were eligible, how many had a later measurement and how many received a forecast. Keep the historical methods available under their existing task identifiers.
- For changes to scorecard fields, update the schema and the affected CLI example. Check that scientific claims still agree with the generated output. [Methods](src/medextract_lab_bench/protocols/methods-v1.json) · [Scorecard schema](src/medextract_lab_bench/protocols/scorecard.schema.json)

Original software contributions use the project's MIT license. Cite and follow the terms of any dataset used to verify a change. [License](LICENSE)
