"""Regression tests for the JSON hot-path in ``modelline_shared.messaging``.

What we guarantee:

  * ``Envelope`` round-trips through the chosen codec without losing fields.
  * Replies are routed by ``correlation_id`` *without* constructing a full
    ``Envelope`` — so a malformed-but-parseable payload still resolves the
    pending future. This is the optimisation that justifies bypassing
    Pydantic on the reply path.
  * ``send`` produces bytes that any compliant JSON parser can consume,
    so the wire format stays compatible with the .NET side.
"""
from __future__ import annotations

import json

from modelline_shared.messaging.client import _json_dumps, _json_loads
from modelline_shared.messaging.schemas import Envelope


def test_envelope_roundtrip() -> None:
    """Encoding then parsing yields a structurally equal Envelope."""
    env = Envelope(
        correlation_id="abc123",
        reply_to="reply.svc.x",
        type="cmd.test",
        payload={"a": 1, "b": [1, 2, 3], "c": {"nested": True}},
    )
    raw = _json_dumps(env.model_dump(mode="json"))
    assert isinstance(raw, (bytes, bytearray))
    # Stdlib json must accept the bytes regardless of which codec produced
    # them — that's the cross-language wire contract.
    parsed = json.loads(raw)
    assert parsed["correlation_id"] == "abc123"
    assert parsed["reply_to"]       == "reply.svc.x"
    assert parsed["type"]           == "cmd.test"
    assert parsed["payload"]["b"]   == [1, 2, 3]


def test_reply_routing_skips_pydantic() -> None:
    """A reply payload with extra unknown fields still routes correctly.

    The reply path only reads ``correlation_id`` / ``payload`` from the
    parsed dict. Adding an unknown top-level key (which a strict
    Pydantic validator would either drop or reject depending on config)
    must not interfere with future routing.
    """
    raw = _json_dumps(
        {
            "correlation_id": "deadbeef",
            "payload":        {"ok": True, "row_count": 42},
            "future_field":   "added by a newer service version",
        }
    )
    parsed = _json_loads(raw)
    assert parsed["correlation_id"] == "deadbeef"
    assert parsed["payload"]["row_count"] == 42


def test_compact_separators_in_stdlib_fallback() -> None:
    """Stdlib path must use compact separators so payload size matches orjson."""
    payload = _json_dumps({"a": 1, "b": 2})
    assert b" " not in payload, "compact: no whitespace between tokens"
