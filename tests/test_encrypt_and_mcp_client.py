"""Token encryption, plus the MCP client's read-only filter, namespacing and output cap."""

import pytest
from cryptography.fernet import Fernet
from langchain_core.tools import StructuredTool

from app.core.encrypt import DecryptionError, FernetTokenCipher
from infra.mcp.client import TOOL_OUTPUT_LIMIT, _cap_content, _is_read_only, _wrap


def test_encrypt_round_trip_and_ciphertext_is_opaque():
    cipher = FernetTokenCipher([Fernet.generate_key().decode()])
    ciphertext = cipher.encrypt("gho_secret")
    assert b"gho_secret" not in ciphertext
    assert cipher.decrypt(ciphertext) == "gho_secret"


def test_key_rotation_old_data_still_decrypts():
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_ciphertext = FernetTokenCipher([old_key]).encrypt("gho_secret")
    rotated = FernetTokenCipher([new_key, old_key])
    assert rotated.decrypt(old_ciphertext) == "gho_secret"
    assert rotated.needs_rotation(old_ciphertext) is True
    assert rotated.needs_rotation(rotated.encrypt("x")) is False


def test_wrong_key_raises_without_leaking():
    ciphertext = FernetTokenCipher([Fernet.generate_key().decode()]).encrypt("gho_secret")
    with pytest.raises(DecryptionError) as raised:
        FernetTokenCipher([Fernet.generate_key().decode()]).decrypt(ciphertext)
    assert "gho_secret" not in str(raised.value)


def _mcp_tool(name: str, read_only: bool | None, output: str = "ok") -> StructuredTool:
    async def call(query: str = ""):
        return [{"type": "text", "text": output}], None

    annotations = {} if read_only is None else {"read_only_hint": read_only}
    return StructuredTool(
        name=name,
        description="d",
        coroutine=call,
        args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        response_format="content_and_artifact",
        metadata={"mcp": {"tool": {"annotations": annotations}}},
    )


def test_read_only_hint_is_read():
    assert _is_read_only(_mcp_tool("a", True)) is True
    assert _is_read_only(_mcp_tool("b", False)) is False
    assert _is_read_only(_mcp_tool("c", None)) is None


async def test_wrapped_tool_is_namespaced_and_output_capped():
    wrapped = _wrap(_mcp_tool("get_file", True, output="x" * (TOOL_OUTPUT_LIMIT + 500)), "github")
    assert wrapped.name == "github__get_file"
    content, _ = await wrapped.coroutine(query="q")
    assert len(content[0]["text"]) < TOOL_OUTPUT_LIMIT + 50
    assert content[0]["text"].endswith("(truncated)")


def test_cap_content_string():
    assert _cap_content("short") == "short"
    assert len(_cap_content("y" * (TOOL_OUTPUT_LIMIT + 1))) < TOOL_OUTPUT_LIMIT + 50
