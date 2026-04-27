"""Async Kafka client with request/reply support.

Built on :mod:`aiokafka`.

Contract:
    * One :class:`KafkaClient` per service instance.
    * `start(subscribe=[...])` boots producer + consumer; the consumer is
      automatically subscribed to the instance's private reply inbox in
      addition to `subscribe`.
    * `register_handler(topic, async_fn)` wires an async handler to a
      topic. If the incoming envelope carries `reply_to`, the handler's
      return value is auto-published back as a reply envelope.
    * `request(topic, payload, timeout=…)` sends a command and awaits the
      reply on the private inbox. Returns the reply payload dict.

JSON hot path:
    Pydantic's ``model_validate_json`` / ``model_dump_json`` go through
    a Rust-backed parser, but they always allocate a full ``Envelope``
    instance even when the consumer only needs the ``correlation_id``
    and ``payload`` (the reply-routing path, which is by far the
    busiest one). We use ``orjson`` when available to do a single C-level
    parse and route replies without instantiating ``Envelope`` at all;
    handler dispatch still constructs a validated ``Envelope`` so user
    code never sees an unvalidated dict. ``orjson`` is an optional
    dependency — if it isn't installed we transparently fall back to
    stdlib ``json``, which is still faster than Pydantic for the
    routing-only path.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from .schemas import Envelope

# Pick the fastest JSON codec we can find. ``orjson`` produces ``bytes``
# directly (no extra encode), which matches what aiokafka wants for
# ``send_and_wait``. The fallback path mimics the same shape so call
# sites stay branch-free.
try:                                            # pragma: no cover — env-dependent
    import orjson  # type: ignore[import-not-found]

    def _json_loads(b: bytes) -> Any:
        return orjson.loads(b)

    def _json_dumps(obj: Any) -> bytes:
        # ``OPT_NON_STR_KEYS`` not needed; envelopes only have str keys.
        # ``OPT_SERIALIZE_NUMPY`` would help analitic responses but they
        # already coerce to native ints/floats before sending.
        return orjson.dumps(obj)

    _JSON_BACKEND = "orjson"
except ImportError:                             # pragma: no cover — fallback only
    import json as _stdlib_json

    def _json_loads(b: bytes) -> Any:
        # ``json`` accepts bytes since 3.6; no need to decode first.
        return _stdlib_json.loads(b)

    def _json_dumps(obj: Any) -> bytes:
        return _stdlib_json.dumps(obj, separators=(",", ":")).encode("utf-8")

    _JSON_BACKEND = "stdlib"

log = logging.getLogger(__name__)

Handler = Callable[[Envelope], Awaitable[dict[str, Any] | None]]


@dataclass
class KafkaClient:
    """Async Kafka client (producer + consumer + request/reply)."""

    bootstrap_servers: str = field(
        default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP_SERVERS", "redpanda:29092")
    )
    service_name: str = "unknown"
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    request_timeout: float = 15.0

    _producer: AIOKafkaProducer | None = None
    _consumer: AIOKafkaConsumer | None = None
    _handlers: dict[str, Handler] = field(default_factory=dict)
    _pending: dict[str, asyncio.Future] = field(default_factory=dict)
    _consume_task: asyncio.Task | None = None
    _reply_inbox: str = ""

    # ── lifecycle ─────────────────────────────────────────────────────────
    @property
    def reply_inbox(self) -> str:
        return self._reply_inbox

    async def start(self, subscribe: list[str] | None = None) -> None:
        self._reply_inbox = f"reply.{self.service_name}.{self.instance_id}"
        topics = list(dict.fromkeys([*(subscribe or []), self._reply_inbox]))

        # Redpanda v24 returns a metadata error for non-existent topics that
        # aiokafka interprets as InvalidPartitionsError instead of auto-creating
        # the topic. consumer.start() then loops forever retrying metadata.
        # Create the reply-inbox (and any other subscribed topics) explicitly
        # via the admin client before starting the consumer. Idempotent:
        # TopicAlreadyExistsError is ignored.
        admin = AIOKafkaAdminClient(bootstrap_servers=self.bootstrap_servers)
        try:
            await admin.start()
            new_topics = [
                NewTopic(name=t, num_partitions=1, replication_factor=1)
                for t in topics
            ]
            try:
                await admin.create_topics(new_topics)
            except TopicAlreadyExistsError:
                pass
            except Exception as exc:  # noqa: BLE001 — best-effort, log and continue
                log.warning("admin.create_topics(%s) failed: %s", topics, exc)
        finally:
            try:
                await admin.close()
            except Exception:  # noqa: BLE001
                pass

        self._producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)
        await self._producer.start()

        self._consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=None,  # broadcast; replies are per-instance anyway
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await self._consumer.start()

        self._consume_task = asyncio.create_task(self._consume_loop())
        log.info(
            "KafkaClient started: service=%s instance=%s topics=%s",
            self.service_name, self.instance_id, topics,
        )

    async def stop(self) -> None:
        if self._consume_task is not None:
            self._consume_task.cancel()
            try:
                await self._consume_task
            except asyncio.CancelledError:
                pass
        if self._consumer is not None:
            await self._consumer.stop()
        if self._producer is not None:
            await self._producer.stop()

    # ── handlers / publish / request-reply ───────────────────────────────
    def register_handler(self, topic: str, handler: Handler) -> None:
        self._handlers[topic] = handler

    async def send(self, topic: str, envelope: Envelope) -> None:
        if self._producer is None:
            raise RuntimeError("KafkaClient.send called before start()")
        # ``model_dump`` returns a dict (no JSON encode), which we hand to
        # the chosen codec. orjson encodes the dict in a single C call;
        # the stdlib fallback uses a compact-separator dump. The wire
        # contract is byte-identical to the previous ``model_dump_json``
        # output (Pydantic also drops whitespace by default), so existing
        # consumers parse the result without any changes.
        payload_bytes = _json_dumps(envelope.model_dump(mode="json"))
        await self._producer.send_and_wait(topic, payload_bytes)

    async def request(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        *,
        type_: str = "request",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a command and await the reply on our private inbox."""
        corr_id = uuid.uuid4().hex
        env = Envelope(
            correlation_id=corr_id,
            reply_to=self._reply_inbox,
            type=type_,
            payload=payload or {},
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[corr_id] = fut
        try:
            await self.send(topic, env)
            return await asyncio.wait_for(fut, timeout=timeout or self.request_timeout)
        finally:
            self._pending.pop(corr_id, None)

    # ── internal ─────────────────────────────────────────────────────────
    async def _dispatch(self, env: "Envelope", handler: Handler) -> None:
        """Run *handler* in its own task so the consume loop stays unblocked.

        This is the fix for the deadlock where a handler that calls
        ``client.request(...)`` would wait forever for a reply that the
        blocked consume loop can never deliver.
        """
        try:
            result = await handler(env)
        except Exception as exc:  # noqa: BLE001 — report via reply envelope
            log.exception("handler for %s raised", env.topic if hasattr(env, "topic") else "?")
            result = {"error": str(exc)}

        if env.reply_to and result is not None:
            reply = Envelope(
                correlation_id=env.correlation_id,
                type=f"{env.type}.reply",
                payload=result,
            )
            await self.send(env.reply_to, reply)

    async def _consume_loop(self) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                # Step 1 — single C-level JSON parse. We *do not* hand the
                # bytes to Pydantic yet: the reply-routing path is the
                # busiest one and only needs ``correlation_id`` /
                # ``payload``, so paying for full Envelope validation on
                # every message would be wasteful. Handler dispatch (the
                # rare path) still gets a fully-validated Envelope below.
                try:
                    raw = _json_loads(msg.value)
                except Exception:
                    log.exception("malformed envelope on %s", msg.topic)
                    continue
                if not isinstance(raw, dict):
                    log.warning("envelope on %s is not a JSON object", msg.topic)
                    continue

                # Reply on our private inbox → resolve pending future
                # without ever constructing an Envelope. The dict is already
                # validated by orjson/json's structural parse; the only
                # fields we touch are well-known optional strings.
                if msg.topic == self._reply_inbox:
                    corr_id = raw.get("correlation_id")
                    if corr_id and corr_id in self._pending:
                        fut = self._pending[corr_id]
                        if not fut.done():
                            payload = raw.get("payload") or {}
                            fut.set_result(payload)
                        continue
                    # Reply for a future we no longer track — drop silently
                    # (caller already timed out or was cancelled).
                    continue

                handler = self._handlers.get(msg.topic)
                if handler is None:
                    continue

                # Step 2 — handler path: validate the envelope properly so
                # user code can rely on the schema invariants.
                try:
                    env = Envelope.model_validate(raw)
                except Exception:
                    log.exception("invalid envelope on %s", msg.topic)
                    continue

                # Fire-and-forget: _dispatch runs the handler concurrently so
                # this loop can immediately move on to the next message.
                # Without this, any handler that calls client.request() would
                # deadlock because the consume loop would be blocked waiting
                # for the handler to finish before it could route the reply.
                asyncio.create_task(self._dispatch(env, handler))
        except asyncio.CancelledError:
            raise
