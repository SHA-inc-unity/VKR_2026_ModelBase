"""Tests for backend.model.config.expand_param_grid."""
from __future__ import annotations

from backend.model.config import expand_param_grid


def test_expand_param_grid_full_product():
    values = {"a": [1, 2], "b": [10, 20]}
    combos = expand_param_grid(values)
    assert len(combos) == 4
    assert {"a": 1, "b": 10} in combos
    assert {"a": 2, "b": 20} in combos


def test_expand_param_grid_with_max_combos():
    values = {"x": list(range(10)), "y": list(range(10))}
    combos = expand_param_grid(values, max_combos=5, seed=42)
    assert len(combos) == 5


def test_expand_param_grid_max_combos_larger_than_total():
    values = {"a": [1, 2], "b": [3, 4]}
    combos = expand_param_grid(values, max_combos=100)
    assert len(combos) == 4  # only 4 combos exist


def test_expand_param_grid_single_param():
    values = {"lr": [0.01, 0.05, 0.1]}
    combos = expand_param_grid(values)
    assert len(combos) == 3
    assert all(set(c.keys()) == {"lr"} for c in combos)


def test_expand_param_grid_reproducible_with_seed():
    values = {"p": list(range(20)), "q": list(range(20))}
    c1 = expand_param_grid(values, max_combos=10, seed=7)
    c2 = expand_param_grid(values, max_combos=10, seed=7)
    assert c1 == c2
