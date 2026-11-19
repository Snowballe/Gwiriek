import json

import pytest

from gwiriek.cli import EXIT_DUE, EXIT_ERROR, EXIT_OK, EXIT_USAGE, main


def run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def run_json(capsys, *argv):
    code, out, err = run(capsys, *argv, "--json")
    assert code == EXIT_OK, err
    return json.loads(out)


@pytest.fixture
def fleet(harness_model, rope_model, make_item, months_ago):
    make_item(
        harness_model,
        label="H-1",
        serial_number="21A0001",
        manufactured_on=months_ago(121),
        purchased_on=months_ago(1),
    )
    make_item(rope_model, label="R-1", manufactured_on=months_ago(12), purchased_on=months_ago(1))


def test_articles_list_human(capsys, fleet):
    code, out, _err = run(capsys, "articles", "list")
    assert code == EXIT_OK
    assert "H-1" in out
    assert "R-1" in out
    assert "Fin de vie" in out  # French interface
    assert "2 article(s)." in out


def test_articles_list_json_and_filters(capsys, fleet):
    data = run_json(capsys, "articles", "list", "--family", "rope")
    assert [i["label"] for i in data] == ["R-1"]
    data = run_json(capsys, "items", "list", "--status", "in_stock", "--search", "21a")
    assert [i["label"] for i in data] == ["H-1"]


def test_articles_show(capsys, fleet):
    data = run_json(capsys, "articles", "show", "21A0001")
    assert data["label"] == "H-1"
    assert data["end_of_life"]["expired"] is True
    code, out, _err = run(capsys, "articles", "show", "H-1")
    assert code == EXIT_OK
    assert "À titre indicatif" in out
    assert "(dépassée)" in out


def test_not_found_exit_code(capsys, db):
    code, out, err = run(capsys, "articles", "show", "nothing")
    assert code == EXIT_ERROR
    assert out == ""
    assert "Aucun article" in err


def test_error_as_json(capsys, db):
    code, _out, err = run(capsys, "articles", "show", "nothing", "--json")
    assert code == EXIT_ERROR
    assert json.loads(err)["error"]["code"] == "not_found"


def test_usage_error_exit_code(capsys, db):
    with pytest.raises(SystemExit) as exc:
        main(["articles", "list", "--status", "lost"])
    assert exc.value.code == EXIT_USAGE
    with pytest.raises(SystemExit) as exc:
        main(["articles", "add", "--model", "1", "--manufactured", "19/11/2026"])
    assert exc.value.code == EXIT_USAGE


def test_add_status_assign(capsys, harness_model, person, months_ago):
    data = run_json(
        capsys,
        "articles",
        "add",
        "--model",
        str(harness_model.id),
        "--serial",
        "22B0042",
        "--label",
        "H-9",
        "--manufactured",
        months_ago(6).isoformat(),
        "--actor",
        "cli:test",
    )
    assert data["status"] == "in_stock"
    assert data["status_history"][0]["actor"] == "cli:test"

    data = run_json(capsys, "articles", "assign", "H-9", "--person", str(person.id))
    assert data["status"] == "in_service"
    assert data["current_assignment"]["person"] == "Yann Le Gall"

    code, _out, err = run(capsys, "articles", "status", "H-9", "quarantine")
    assert code == EXIT_ERROR
    assert "motif" in err.lower()

    data = run_json(capsys, "articles", "status", "H-9", "quarantine", "--reason", "Chute")
    assert data["status"] == "quarantine"

    data = run_json(capsys, "articles", "assign", "H-9", "--stock")
    assert data["current_assignment"]["kind"] == "stock"


def test_add_invalid_data(capsys, harness_model):
    code, _out, err = run(capsys, "articles", "add", "--model", str(harness_model.id))
    assert code == EXIT_ERROR
    assert "série" in err


def test_due(capsys, fleet):
    data = run_json(capsys, "due", "--within", "30")
    assert [e["item"]["label"] for e in data["overdue"]] == ["H-1"]
    code, out, _err = run(capsys, "due")
    assert code == EXIT_OK
    assert "En retard (fin de vie ou vérification): 1" in out
    code, _out, _err = run(capsys, "due", "--check")
    assert code == EXIT_DUE


def test_due_check_nothing(capsys, db):
    code, _out, _err = run(capsys, "due", "--check")
    assert code == EXIT_OK


def test_catalog(capsys, petzl):
    data = run_json(
        capsys,
        "catalog",
        "add-model",
        "--manufacturer",
        "Petzl",
        "--name",
        "ASAP",
        "--family",
        "backup",
        "--standard",
        "en353-2",
        "--unlimited",
        "--check",
        "Corps",
        "--check",
        "Came",
    )
    assert data["standards"] == ["EN 353-2"]
    assert data["checklist"] == ["Corps", "Came"]
    assert [m["name"] for m in run_json(capsys, "catalog", "models", "--family", "backup")] == [
        "ASAP"
    ]
    assert run_json(capsys, "catalog", "show", str(data["id"]))["lifetime_unlimited"] is True
    code, out, _err = run(capsys, "catalog", "show", str(data["id"]))
    assert "1. Corps" in out
    run_json(capsys, "catalog", "add-manufacturer", "Kong")
    assert [m["name"] for m in run_json(capsys, "catalog", "manufacturers")] == ["Kong", "Petzl"]
    code, _out, _err = run(capsys, "catalog", "add-manufacturer", "Kong")
    assert code == EXIT_ERROR


def test_people(capsys, db):
    data = run_json(capsys, "people", "add", "Gwenn", "Kerjean", "--email", "g@example.org")
    assert data["email"] == "g@example.org"
    assert [p["last_name"] for p in run_json(capsys, "people", "list")] == ["Kerjean"]


def test_manage_passthrough(capsys, db):
    code = main(["manage", "check"])
    out, _err = capsys.readouterr()
    assert code == EXIT_OK
    assert "no issues" in out or "aucun problème" in out.lower()


def test_load_demo_data_is_idempotent(capsys, db):
    assert main(["manage", "load_demo_data"]) == EXIT_OK
    assert main(["manage", "load_demo_data"]) == EXIT_OK
    out, _err = capsys.readouterr()
    assert "already present" in out
    data = run_json(capsys, "due")

    def by_label(section):
        return {e["item"]["label"]: e for e in data[section]}

    overdue = by_label("overdue")
    assert overdue["DEMO-K01"]["reasons"][0] == "end_of_life_passed"
    assert overdue["DEMO-H02"]["reasons"] == ["inspection_overdue"]  # inspected 13 months ago
    assert "DEMO-C02" in overdue  # stock never inspected
    upcoming = by_label("upcoming")
    assert set(upcoming) == {"DEMO-H01", "DEMO-R01"}
    assert upcoming["DEMO-R01"]["deadline_kind"] == "inspection"
    assert upcoming["DEMO-H01"]["inspection_due_on"] == upcoming["DEMO-H01"]["end_of_life"]
    assert set(by_label("undetermined")) == {"DEMO-A01", "DEMO-D01"}
    inspectors = run_json(capsys, "inspectors", "list")
    assert [i["qualified"] for i in inspectors] == [True, True, False]
    assert len(run_json(capsys, "inspections", "list")) == 4


# --- Inspectors, inspections, notify ----------------------------------------------------------


def test_inspectors(capsys, person, months_ago, tmp_path):
    document = tmp_path / "habilitation.pdf"
    document.write_bytes(b"%PDF")
    data = run_json(
        capsys,
        "inspectors",
        "add",
        "--person",
        str(person.id),
        "--authorized-on",
        months_ago(1).isoformat(),
        "--document",
        str(document),
        "--family",
        "harness",
        "--family",
        "rope",
    )
    assert data["kind"] == "internal"
    assert data["qualified"] is True
    assert data["authorization_document"].endswith(".pdf")
    data = run_json(
        capsys,
        "inspectors",
        "add",
        "--name",
        "Erwan",
        "--organisation",
        "Bureau",
        "--authorized-on",
        months_ago(1).isoformat(),
        "--reference",
        "C-1",
        "--all-families",
    )
    assert len(data["families"]) == 10
    assert run_json(capsys, "inspectors", "show", str(data["id"]))["name"] == "Erwan (Bureau)"
    code, out, _err = run(capsys, "inspectors", "list")
    assert code == EXIT_OK
    assert "Erwan (Bureau)" in out
    code, out, _err = run(capsys, "inspectors", "export")
    assert out.splitlines()[0].startswith("ID;Nom;")
    assert len(out.splitlines()) == 3
    assert len(run_json(capsys, "inspectors", "export", "--all")) == 2
    code, _out, err = run(
        capsys, "inspectors", "add", "--name", "X", "--authorized-on", "2026-01-01"
    )
    assert code == EXIT_ERROR
    assert "référence" in err


def test_inspections(capsys, harness_model, make_item, inspector, months_ago, tmp_path):
    make_item(harness_model, label="H-5", purchased_on=months_ago(13))
    code, _out, _err = run(capsys, "due", "--check", "--inspections-only")
    assert code == EXIT_DUE

    code, _out, err = run(
        capsys, "inspections", "add", "H-5", "--inspector", str(inspector.id),
        "--decision", "valid", "--point", "Sangles=ok", "--point", "2=nok",
    )  # fmt: skip
    assert code == EXIT_ERROR
    assert "quarantaine" in err

    code, _out, err = run(
        capsys, "inspections", "add", "H-5", "--inspector", str(inspector.id),
        "--decision", "valid", "--point", "Sangles=ok", "--point", "1=ok",
    )  # fmt: skip
    assert code == EXIT_ERROR
    assert "deux fois" in err

    report = tmp_path / "rapport.pdf"
    report.write_bytes(b"%PDF")
    data = run_json(
        capsys, "inspections", "add", "H-5", "--inspector", str(inspector.id),
        "--decision", "watch", "--point", "Sangles=ok", "--point", "Coutures=na",
        "--comment", "Usure", "--next", months_ago(-6).isoformat(),
        "--date", months_ago(1).isoformat(), "--report", str(report), "--actor", "cli:test",
    )  # fmt: skip
    assert data["decision"] == "watch"
    assert data["resulting_status"] == "watch"
    assert data["next_due_on"] == months_ago(-6).isoformat()
    assert data["actor"] == "cli:test"
    assert data["report"].endswith("rapport.pdf")

    code, out, _err = run(capsys, "inspections", "show", str(data["id"]))
    assert "[Conforme] Sangles" in out
    assert "[Sans objet] Coutures" in out
    code, out, _err = run(capsys, "inspections", "list", "--item", "H-5")
    assert "1 vérification(s)." in out
    listed = run_json(capsys, "inspections", "list", "--since", months_ago(0).isoformat())
    assert listed == []
    code, out, _err = run(capsys, "articles", "show", "H-5")
    assert "Prochaine vérification" in out
    assert "Valide, à surveiller" in out
    code, _out, _err = run(capsys, "due", "--check", "--inspections-only", "--within", "30")
    assert code == EXIT_OK


def test_inspections_usage_errors(capsys, db):
    with pytest.raises(SystemExit) as exc:
        main(["inspections", "add", "X", "--inspector", "1", "--decision", "valid", "--point", "x"])
    assert exc.value.code == EXIT_USAGE
    with pytest.raises(SystemExit) as exc:
        main(["inspectors", "add", "--name", "X", "--document", "/nonexistent/file.pdf"])
    assert exc.value.code == EXIT_USAGE


def test_notify(capsys, settings, harness_model, make_item, months_ago):
    from django.core import mail

    settings.GWIRIEK_ALERT_RECIPIENTS = []
    code, _out, err = run(capsys, "notify")
    assert code == EXIT_ERROR
    assert "GWIRIEK_ALERT_RECIPIENTS" in err
    settings.GWIRIEK_ALERT_RECIPIENTS = ["chef@example.org"]
    code, out, _err = run(capsys, "notify")
    assert "Rien" in out
    make_item(harness_model, label="H-1", purchased_on=months_ago(13))
    assert run_json(capsys, "notify")["status"] == "sent"
    code, out, _err = run(capsys, "notify")
    assert "--force" in out
    assert run_json(capsys, "notify", "--force", "--to", "x@example.org")["status"] == "sent"
    assert [m.to for m in mail.outbox] == [["chef@example.org"], ["x@example.org"]]
