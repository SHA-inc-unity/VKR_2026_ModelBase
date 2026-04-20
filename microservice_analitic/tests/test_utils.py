"""Юнит-тесты для backend.utils."""
from __future__ import annotations

import json
import re

import numpy as np

from backend.utils import now_utc, to_json_safe


def test_to_json_safe_numpy_scalars():
    assert to_json_safe(np.int64(7)) == 7
    assert isinstance(to_json_safe(np.int64(7)), int)
    assert to_json_safe(np.float32(1.5)) == 1.5
    assert isinstance(to_json_safe(np.float32(1.5)), float)
    assert to_json_safe(np.bool_(True)) is True


def test_to_json_safe_ndarray():
    arr = np.array([1, 2, 3], dtype=np.int32)
    result = to_json_safe(arr)
    assert result == [1, 2, 3]
    assert all(isinstance(x, int) for x in result)


def test_to_json_safe_nested_dict_and_list():
    payload = {
        "a": np.int64(1),
        "b": [np.float64(0.5), np.float64(1.5)],
        "c": {"nested": np.array([1.0, 2.0])},
        "d": "plain",
    }
    safe = to_json_safe(payload)
    # Должно быть сериализуемо стандартным json без ошибок TypeError.
    serialized = json.dumps(safe)
    back = json.loads(serialized)
    assert back == {"a": 1, "b": [0.5, 1.5], "c": {"nested": [1.0, 2.0]}, "d": "plain"}


def test_to_json_safe_passthrough_primitives():
    assert to_json_safe(42) == 42
    assert to_json_safe("hello") == "hello"
    assert to_json_safe(None) is None


def test_now_utc_format():
    ts = now_utc()
    # ISO-8601 секундной точности с суффиксом Z: 2024-01-15T12:34:56Z
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts


def test_now_utc_monotonic():
    a = now_utc()
    b = now_utc()
    assert a <= b  # лексикографический порядок совпадает с временным для ISO-8601
