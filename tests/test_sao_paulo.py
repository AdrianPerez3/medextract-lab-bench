import unittest
from datetime import date, timedelta

from medextract_lab_bench.adapters.sao_paulo import _assemble


def row(key, day, value, **fields):
    return {
        "ID_PACIENTE": key,
        "DT_COLETA": (date(2020, 1, 1) + timedelta(days=day)).strftime("%d/%m/%Y"),
        "DE_EXAME": "Hemograma com Plaquetas",
        "DE_ANALITO": "Hemoglobina",
        "CD_UNIDADE": "g/dL",
        "DE_RESULTADO": value,
        "DE_ORIGEM": "HOSP",
        **fields,
    }


class CohortRules(unittest.TestCase):
    def test_boundaries_ties_and_assay_exclusions(self):
        people = [{"ID_PACIENTE": k} for k in "abcde"]
        exams = [row(k, d, "12,5") for k in "abcd" for d in range(4)]
        exams += [row("a", 3, "16.5"), row("a", 89, "10"), row("a", 97, "11")]
        exams += [
            row("b", 79, "8"),
            row("c", 107, "9"),
            row("d", 78, "8"),
            row("d", 108, "9"),
        ]
        exams += [
            row("a", 93, "99", DE_EXAME="Urina Tipo I"),
            row("b", 93, "20", CD_UNIDADE="NULL"),
            row("c", 93, "<12"),
            row("z", 93, "8"),
            row("e", 0, "7"),
        ]
        counts, histories = _assemble(people, reversed(exams))
        self.assertEqual(
            (
                counts["roster"],
                counts["with_one_or_more_days"],
                counts["eligible"],
                counts["paired"],
            ),
            (5, 5, 4, 3),
        )
        self.assertEqual(counts["row_flow"]["days_with_multiple_rows"], 1)
        self.assertEqual(histories[0][1][1][-1], 14.5)
        self.assertEqual([h[2] for _, h in histories], [10, 8, 9, None])
        for kind in ("unit", "value", "unlinked"):
            self.assertEqual(counts["row_flow"]["excluded_" + kind], 1)

    def test_administrative_windows(self):
        people = [{"ID_PACIENTE": k} for k in "abc"]
        exams = [
            row(k, base + d, "10")
            for k, base in zip("abc", [0, 10, 40])
            for d in range(4)
        ]
        exams += [row("b", 107, "9")]
        counts, _ = _assemble(people, exams)
        self.assertEqual(
            counts["window_groups"],
            {
                "complete": {"eligible": 1, "paired": 0},
                "partial": {"eligible": 1, "paired": 1},
                "unobservable": {"eligible": 1, "paired": 0},
            },
        )


def test_cli_rejects_unrecognized_archive(tmp_path, capsys):
    from medextract_lab_bench.cli import main

    archive = tmp_path / "invented.zip"
    archive.write_bytes(b"invented")
    out = tmp_path / "report.json"
    assert main(["replay-sao-paulo", "--archive", str(archive), "--out", str(out)]) == 1
    assert "SHA-256" in capsys.readouterr().err
    assert not out.exists()


def test_verify_shipped_sao_paulo_result(tmp_path, capsys):
    from importlib.resources import files
    import json
    from medextract_lab_bench.cli import main

    score = json.loads(
        files("medextract_lab_bench")
        .joinpath("protocols", "sao-paulo-hemoglobin-results.json")
        .read_text()
    )
    assert (
        score["qualification"]["eligible"],
        score["all"]["paired"],
        score["complete"]["paired"],
    ) == (3944, 700, 638)
    path = tmp_path / "score.json"
    path.write_text(json.dumps(score))
    args = [
        "verify",
        "--task",
        "sao_paulo_hemoglobin_day90_v1",
        "--scorecard",
        str(path),
    ]
    assert main(args) == 0
    assert (
        json.loads(capsys.readouterr().out)["verification"]
        == "matched_external_scorecard"
    )
    score["all"]["model_minus"]["last"]["ci95"][0] = 0
    path.write_text(json.dumps(score))
    assert main(args) == 1
    assert "ci95" in capsys.readouterr().err


def test_replay_invented_zip_through_cli(tmp_path, monkeypatch):
    from csv import DictWriter
    from hashlib import sha256
    from io import StringIO
    import json
    from zipfile import ZipFile
    from medextract_lab_bench.adapters import sao_paulo
    from medextract_lab_bench.cli import main

    def table(rows):
        stream = StringIO()
        writer = DictWriter(stream, fieldnames=list(rows[0]), delimiter="|")
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue()

    key = "INVENTED-FOR-TEST"
    archive = tmp_path / "invented.zip"
    with ZipFile(archive, "w") as z:
        z.writestr(sao_paulo.ROSTER, table([{"ID_PACIENTE": key}]))
        labs = [row(key, day, "12,5") for day in [0, 1, 2, 3]]
        labs.extend([row(key, 93, "11"), row(key, 107, "11", DE_EXAME="Other")])
        z.writestr(sao_paulo.EXAMS, table(labs))
    monkeypatch.setattr(sao_paulo, "ZIP_SHA", sha256(archive.read_bytes()).hexdigest())
    out = tmp_path / "score.json"
    assert main(["replay-sao-paulo", "--archive", str(archive), "--out", str(out)]) == 0
    assert key not in out.read_text()
    score = json.loads(out.read_text())
    assert score["qualification"]["row_flow"]["all_exam_rows"] == 6
    assert score["complete"] == score["all"]
    assert (score["all"]["eligible"], score["all"]["paired"]) == (1, 1)
    assert score["all"]["methods"]["last"]["metrics"]["mae"] == 1.5
