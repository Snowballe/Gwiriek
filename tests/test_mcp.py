"""MCP server tests: in-memory client of the SDK, and a real stdio session."""

import os
import subprocess
import sys
from typing import get_args

import pytest
from asgiref.sync import sync_to_async
from mcp import Client, StdioServerParameters

from gwiriek import mcp_server
from gwiriek.equipment.models import Family, ItemStatus
from gwiriek.mcp_server import build_server

EXPECTED_TOOLS = {
    "search_items",
    "get_item",
    "list_due_items",
    "list_manufacturers",
    "search_product_models",
    "get_product_model",
    "list_people",
    "create_item",
}

# Tools run in worker threads: data must be committed to be visible there, and the ORM
# cannot be called from the event loop (hence sync_to_async in test bodies).
pytestmark = [pytest.mark.anyio, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(build_server(), raise_exceptions=True) as c:
        yield c


def test_literals_match_model_choices():
    assert set(get_args(mcp_server.Status)) == set(ItemStatus.values)
    assert set(get_args(mcp_server.InitialStatus)) == set(ItemStatus.values) - {"retired"}
    assert set(get_args(mcp_server.FamilyName)) == set(Family.values)


async def test_tools_are_listed_without_deletion(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    assert not any("delete" in name or "remove" in name for name in tools)
    assert tools["get_item"].annotations.read_only_hint is True
    assert tools["create_item"].annotations.read_only_hint is False
    assert tools["create_item"].annotations.destructive_hint is False
    assert tools["create_item"].output_schema is not None
    status_schema = tools["create_item"].input_schema["properties"]["status"]
    assert set(status_schema["enum"]) == set(ItemStatus.values) - {"retired"}


async def test_search_and_get_item(client, harness_model, make_item, months_ago):
    await sync_to_async(make_item)(
        harness_model, label="H-1", serial_number="21A0001", manufactured_on=months_ago(121)
    )
    result = await client.call_tool("search_items", {"query": "avao"})
    assert not result.is_error
    assert [i["label"] for i in result.structured_content["result"]] == ["H-1"]

    result = await client.call_tool("get_item", {"ref": "21A0001"})
    eol = result.structured_content["end_of_life"]
    assert eol["expired"] is True
    assert eol["end_date"] == months_ago(1).isoformat()
    assert "notice" in eol["disclaimer"]


async def test_business_error_is_a_tool_error(client, db):
    result = await client.call_tool("get_item", {"ref": "nothing"})
    assert result.is_error
    assert "[not_found]" in result.content[0].text
    assert result.structured_content is None


async def test_invalid_arguments_are_rejected(client, db):
    result = await client.call_tool("list_due_items", {"within_days": -5})
    assert result.is_error
    result = await client.call_tool("search_items", {"status": ["lost"]})
    assert result.is_error


async def test_create_item(client, rope_model, months_ago):
    result = await client.call_tool(
        "create_item",
        {
            "product_model_id": rope_model.id,
            "serial_number": "B21-1",
            "manufactured_on": months_ago(3).isoformat(),
            "rope_diameter_mm": 10.5,
            "rope_type": "A",
        },
    )
    assert not result.is_error, result.content
    data = result.structured_content
    assert data["rope_diameter_mm"] == 10.5
    assert data["status_history"][0]["actor"] == "mcp"
    assert data["end_of_life"]["limited_by"] == ["storage"]


async def test_create_item_validation_error(client, harness_model):
    result = await client.call_tool("create_item", {"product_model_id": harness_model.id})
    assert result.is_error
    assert "[invalid]" in result.content[0].text


async def test_catalogue_people_and_due(client, harness_model, person, make_item, months_ago):
    await sync_to_async(make_item)(harness_model, label="SOON", manufactured_on=months_ago(119))
    manufacturers = await client.call_tool("list_manufacturers", {})
    assert [m["name"] for m in manufacturers.structured_content["result"]] == ["Petzl"]
    models = await client.call_tool("search_product_models", {"family": "harness"})
    [model] = models.structured_content["result"]
    detail = await client.call_tool("get_product_model", {"product_model_id": model["id"]})
    assert detail.structured_content["checklist"] == ["Sangles", "Coutures"]
    people = await client.call_tool("list_people", {})
    assert [p["last_name"] for p in people.structured_content["result"]] == ["Le Gall"]
    report = await client.call_tool("list_due_items", {"within_days": 60})
    assert [e["item"]["label"] for e in report.structured_content["upcoming"]] == ["SOON"]


@pytest.mark.django_db(transaction=False)
async def test_real_stdio_session(tmp_path):
    """Launch ``gwiriek mcp`` as a subprocess and talk to it over stdio."""
    env = {
        "GWIRIEK_DATABASE_URL": f"sqlite:///{tmp_path / 'stdio.sqlite3'}",
        "DJANGO_SETTINGS_MODULE": "gwiriek.settings",
    }
    command = [sys.executable, "-m", "gwiriek.cli"]
    for args in (["manage", "migrate", "-v0"], ["manage", "load_demo_data"]):
        subprocess.run(
            [*command, *args], env={**os.environ, **env}, check=True, capture_output=True
        )

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "gwiriek.cli", "mcp"], env=env
    )
    async with Client(params) as stdio_client:
        tools = await stdio_client.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED_TOOLS
        result = await stdio_client.call_tool("list_due_items", {"within_days": 90})
        assert [e["item"]["label"] for e in result.structured_content["overdue"]] == ["DEMO-K01"]
        result = await stdio_client.call_tool("get_item", {"ref": "DEMO-Q01"})
        assert result.structured_content["status"] == "quarantine"
