import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from medextract_lab_bench.cli import main
from medextract_lab_bench.receipts import compare_golden, read_json, write_once


def test_synthetic_cli(tmp_path):
    out = tmp_path / "out.json"
    assert main(["run", "--task", "synthetic-demo", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["eligible_people"] >= data["paired_people"] > 0
    assert data["task"] == "synthetic-demo"
    original = out.read_bytes()
    assert main(["run", "--task", "synthetic-demo", "--out", str(out)]) != 0
    assert out.read_bytes() == original


def test_unexpected_extra_and_nan_and_count_types_rejected():
    with pytest.raises(ValueError):
        compare_golden({"m": 1}, {"m": True})
    with pytest.raises(ValueError):
        compare_golden({"m": 1}, {"m": 1, "other": 0})
    with pytest.raises(ValueError):
        compare_golden({"mae": 1.5}, {"mae": float("nan")})
    with pytest.raises(ValueError):
        compare_golden({"mae": 1.5}, {"mae": 1.51})


def test_concurrent_write_once(tmp_path):
    path = tmp_path / "receipt.json"

    def save(index):
        try:
            write_once(path, {"value": index})
            return True
        except FileExistsError:
            return False

    with ThreadPoolExecutor(4) as workers:
        assert sum(workers.map(save, range(12))) == 1
    assert read_json(path)["value"] in range(12)
    assert list(tmp_path.iterdir()) == [path]


def test_failed_serialization_leaves_no_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    with pytest.raises(ValueError):
        write_once(path, {"value": float("inf")})
    assert not list(tmp_path.iterdir())


def test_reader_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text('{"people": 1, "people": 99}')
    with pytest.raises(ValueError):
        read_json(path)


def test_corrected_demo_validates_against_shipped_schema(tmp_path):
    import jsonschema
    from importlib.resources import files
    from medextract_lab_bench.replay import demo

    schema = read_json(
        files("medextract_lab_bench").joinpath("protocols", "scorecard.schema.json")
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(demo(), schema)


def test_verify_seoul_matches_all_fields_and_rejects_changed_bias(tmp_path, capsys):
    from importlib.resources import files

    source = files("medextract_lab_bench").joinpath(
        "protocols", "vitaldb-hemoglobin-results.json"
    )
    output = tmp_path / "seoul.json"
    score = read_json(source)
    assert (score["primary"]["paired"], score["primary"]["eligible"]) == (55, 226)
    output.write_text(json.dumps(score))
    args = [
        "verify",
        "--task",
        "vitaldb_hemoglobin_day90_v1",
        "--scorecard",
        str(output),
    ]
    assert main(args) == 0
    assert (
        json.loads(capsys.readouterr().out)["verification"]
        == "matched_external_scorecard"
    )
    score["primary"]["methods"]["model"]["metrics"]["bias"] = 0
    output.write_text(json.dumps(score))
    assert main(args) == 1
    assert "bias" in capsys.readouterr().err


def test_boston_demo_cli_uses_invented_events(tmp_path):
    pytest.importorskip("duckdb")
    output = tmp_path / "invented.json"
    args = ["run", "--task", "synthetic-boston", "--out", str(output)]
    assert main(args) == 0
    receipt = read_json(output)
    assert receipt["release"] == "invented"
    assert (receipt["scorecard"]["eligible"], receipt["scorecard"]["paired"]) == (3, 2)
    assert receipt["scorecard"]["methods"]["last"]["metrics"]["mae"] == pytest.approx(
        1.5
    )
    assert main(args) != 0
