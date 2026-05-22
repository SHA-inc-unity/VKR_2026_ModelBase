from __future__ import annotations

import asyncio

from backend.dataset.repair import load_ohlcv, recompute_features
from modelline_shared.messaging.topics import (
    CMD_DATA_DATASET_COMPUTE_FEATURES,
    CMD_DATA_DATASET_MAKE_TABLE,
    CMD_DATA_DATASET_REPAIR_OHLCV,
)


def test_load_ohlcv_delegates_exchange_aware_repair_to_data_service():
    calls: list[tuple[str, dict, dict]] = []

    async def request(topic: str, payload: dict, **kwargs):
        calls.append((topic, payload, kwargs))
        return {"table": "kraken_btcusdt_1m", "rows_affected": 42}

    async def publish(_topic, _envelope):
        return None

    reply = asyncio.run(load_ohlcv(
        symbol="btcusdt",
        timeframe="1m",
        exchange="kraken",
        start_ms=1000,
        end_ms=2000,
        correlation_id="repair-cid",
        request=request,
        publish=publish,
    ))

    assert reply["rows_affected"] == 42
    assert calls == [(
        CMD_DATA_DATASET_REPAIR_OHLCV,
        {
            "symbol": "btcusdt",
            "timeframe": "1m",
            "exchange": "kraken",
            "start_ms": 1000,
            "end_ms": 2000,
            "progress_correlation_id": "repair-cid",
        },
        {"timeout": 600.0},
    )]


def test_recompute_features_resolves_exchange_aware_table_name():
    calls: list[tuple[str, dict, dict]] = []

    async def request(topic: str, payload: dict, **kwargs):
        calls.append((topic, payload, kwargs))
        if topic == CMD_DATA_DATASET_MAKE_TABLE:
            return {"table_name": "binance_btcusdt_60m"}
        if topic == CMD_DATA_DATASET_COMPUTE_FEATURES:
            return {"rows_updated": 7}
        raise AssertionError(f"Unexpected topic {topic}")

    async def publish(_topic, _envelope):
        return None

    reply = asyncio.run(recompute_features(
        symbol="btcusdt",
        timeframe="60m",
        exchange="binance",
        correlation_id="repair-cid",
        request=request,
        publish=publish,
    ))

    assert reply["table"] == "binance_btcusdt_60m"
    assert reply["rows_updated"] == 7
    assert calls[0] == (
        CMD_DATA_DATASET_MAKE_TABLE,
        {"symbol": "btcusdt", "timeframe": "60m", "exchange": "binance"},
        {},
    )
    assert calls[1] == (
        CMD_DATA_DATASET_COMPUTE_FEATURES,
        {"table": "binance_btcusdt_60m"},
        {"timeout": 600.0},
    )