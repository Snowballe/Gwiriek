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
    make_item(harness_model, label="H-1", serial_number="21A0001", manufactured_on=months_ago(121))
    make_item(rope_model, label="R-1", manufactured_on=months_ago(12))


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
    assert "Fin de vie dépassée : 1" in out or "Fin de vie dépassée: 1" in out
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
    assert [e["item"]["label"] for e in data["overdue"]] == ["DEMO-K01"]
    assert {e["item"]["label"] for e in data["upcoming"]} == {"DEMO-H01", "DEMO-R02"}
    assert {e["item"]["label"] for e in data["undetermined"]} == {"DEMO-A01", "DEMO-D01"}
