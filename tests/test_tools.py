import json

from cutflow.agent_tools.tools import validate_cutflow_json_tool, get_cutflow_schema, get_tool_definitions
from cutflow.agent_tools.schemas import TOOL_DEFINITIONS


def test_validate_tool_valid():
    j = json.dumps({"project": {"width": 1920, "height": 1080, "fps": 30}, "tracks": []})
    result = validate_cutflow_json_tool(j)
    assert result["valid"] is True


def test_validate_tool_invalid():
    result = validate_cutflow_json_tool('{"project": {"fps": -1}}')
    assert result["valid"] is False


def test_tool_definitions_count():
    defs = get_tool_definitions()
    assert len(defs) == 7
    names = {d["function"]["name"] for d in defs}
    assert "inspect_media" in names
    assert "render_cutflow_json" in names


def test_schema_has_properties():
    schema = get_cutflow_schema()
    assert "properties" in schema
    assert "project" in schema["properties"] or "tracks" in schema["properties"]
