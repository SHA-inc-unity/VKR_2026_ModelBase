#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import random
import ssl
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


VERSION = "1.0"
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}

STATUS_COLORS = {
    PASS: "green",
    FAIL: "red",
    SKIP: "yellow",
}

SERVICE_COLORS = {
    "infra": "magenta",
    "data": "blue",
    "analytics": "cyan",
    "gateway": "green",
    "news": "yellow",
    "notification": "magenta",
    "social": "blue",
    "account": "cyan",
    "admin": "white",
}

SERVICE_LABELS = {
    "infra": "Infrastructure",
    "data": "Data Service",
    "analytics": "Analytics",
    "gateway": "Gateway",
    "news": "News",
    "notification": "Notifications",
    "social": "Social",
    "account": "Account",
    "admin": "Admin",
}

STEP_LABELS = {
    ("infra", "health"): "Liveness",
    ("infra", "ready"): "Readiness",
    ("data", "health"): "Liveness",
    ("analytics", "health"): "Liveness",
    ("analytics", "registry"): "Registry",
    ("gateway", "health"): "Liveness",
    ("gateway", "market_config"): "Market Config",
    ("gateway", "market_chart"): "Candles",
    ("news", "list"): "Feed",
    ("notification", "unread_count"): "Unread Count",
    ("notification", "get_settings"): "Get Settings",
    ("notification", "update_settings"): "Update Settings",
    ("notification", "list"): "List",
    ("social", "list_comments"): "Comments",
    ("social", "favorite_add"): "Add Favorite",
    ("social", "favorite_list"): "Favorites",
    ("social", "comment_create"): "Create Comment",
    ("social", "comment_like"): "Like Comment",
    ("social", "comment_unlike"): "Unlike Comment",
    ("social", "comment_delete"): "Delete Comment",
    ("social", "favorite_remove"): "Remove Favorite",
    ("account", "register"): "Register",
    ("account", "login"): "Login",
    ("account", "me"): "Profile",
    ("account", "get_settings"): "Get Settings",
    ("account", "update_settings"): "Update Settings",
    ("account", "refresh"): "Refresh Token",
    ("account", "logout"): "Logout",
    ("account", "delete_account"): "Delete Account",
    ("admin", "page"): "Page",
    ("admin", "api_health"): "API Health",
}


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def normalize_base(url: str | None) -> str | None:
    if url is None:
        return None
    text = url.strip()
    if not text or text.lower() in {"none", "skip", "disabled"}:
        return None
    return text.rstrip("/")


def truncate(value: str, limit: int = 240) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def normalize_service_base(url: str | None, default_scheme: str = "http") -> str | None:
    text = normalize_base(url)
    if text is None:
        return None
    if "://" in text:
        return text
    return f"{default_scheme}://{text}"


def read_simple_env_file(file_path: Path) -> dict[str, str]:
    if not file_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        text = value.strip()
        if text:
            return text
    return None


def extract_host_from_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlparse(value.strip() if "://" in value else f"http://{value.strip()}")
    return parsed.hostname


def random_suffix(size: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(size))


class TraceFailure(RuntimeError):
    pass


@dataclass
class HttpResponseData:
    status: int
    headers: dict[str, str]
    text: str
    json_body: Any


@dataclass
class StepResult:
    service: str
    name: str
    status: str
    method: str | None
    url: str | None
    http_status: int | None
    duration_ms: int
    detail: str


@dataclass
class RunnerConfig:
    backend_host: str
    timeout_seconds: float
    insecure_https: bool
    color_mode: str
    verbose_output: bool
    infra_base_url: str | None
    admin_base_url: str | None
    account_base_url: str | None
    data_base_url: str | None
    analytics_base_url: str | None
    gateway_base_url: str | None
    news_base_url: str | None
    notification_base_url: str | None
    social_base_url: str | None
    target_symbol: str
    timeframe: str
    candle_limit: int
    comment_target_type: str
    comment_target_id: str
    only_services: set[str]
    strict: bool
    json_report: str | None


class TraceRunner:
    def __init__(self, config: RunnerConfig):
        self.config = config
        self.results: list[StepResult] = []
        self.account_auth: dict[str, Any] | None = None
        self.account_profile: dict[str, Any] | None = None
        self.notification_settings: dict[str, Any] | None = None
        self.created_comment_id: str | None = None
        self.account_email: str | None = None
        self.account_password: str | None = None
        self.account_username: str | None = None
        self.last_service: str | None = None
        self.ssl_context = ssl._create_unverified_context() if config.insecure_https else None
        self.use_color = self.should_use_color()

    def should_use_color(self) -> bool:
        if self.config.color_mode == "always":
            return True
        if self.config.color_mode == "never":
            return False
        if os.getenv("NO_COLOR"):
            return False
        return sys.stdout.isatty()

    def paint(self, text: str, *styles: str) -> str:
        if not self.use_color or not styles:
            return text
        prefix = "".join(ANSI[style] for style in styles)
        return f"{prefix}{text}{ANSI['reset']}"

    def service_label(self, service: str) -> str:
        return SERVICE_LABELS.get(service, service.replace("_", " ").title())

    def step_label(self, service: str, name: str) -> str:
        label = STEP_LABELS.get((service, name))
        if label:
            return label
        return name.replace("_", " ").title()

    def print_service_header(self, service: str) -> None:
        color = SERVICE_COLORS.get(service, "white")
        print()
        print(self.paint(f"== {self.service_label(service)} ==", "bold", color))

    def print_header(self) -> None:
        print(self.paint("ModelLine Service Tracer", "bold", "cyan"))
        print(self.paint(f"backend: {self.config.backend_host}", "dim"))
        mode = "verbose" if self.config.verbose_output else "presentation"
        print(self.paint(f"timeout: {self.config.timeout_seconds}s   https-insecure: {self.config.insecure_https}   output: {mode}", "dim"))
        print()

    def log_result(
        self,
        *,
        service: str,
        name: str,
        status: str,
        duration_ms: int,
        detail: str,
        method: str | None = None,
        url: str | None = None,
        http_status: int | None = None,
    ) -> None:
        step = StepResult(
            service=service,
            name=name,
            status=status,
            method=method,
            url=url,
            http_status=http_status,
            duration_ms=duration_ms,
            detail=detail,
        )
        self.results.append(step)
        if self.last_service != service:
            self.print_service_header(service)
            self.last_service = service

        if self.config.verbose_output:
            http_part = f" http={http_status}" if http_status is not None else ""
            method_part = f" {method}" if method else ""
            url_part = f" {url}" if url else ""
            status_label = self.paint(f"[{status}]", STATUS_COLORS.get(status, "white"), "bold")
            print(f"{status_label} {service}.{name}{method_part}{url_part}{http_part} {duration_ms}ms :: {self.paint(detail, 'dim')}")
            return

        status_label = self.paint(status.ljust(4), STATUS_COLORS.get(status, "white"), "bold")
        step_label = self.paint(self.step_label(service, name).ljust(18), "bold")
        timing = self.paint(f"{duration_ms:>4} ms", "cyan")
        http_label = self.paint(f"HTTP {http_status}" if http_status is not None else "", "dim")
        detail_label = self.paint(detail, "dim")
        print(f" {status_label}  {step_label}  {timing}  {http_label:<10}  {detail_label}")

    def skip(self, service: str, name: str, detail: str) -> None:
        self.log_result(service=service, name=name, status=SKIP, duration_ms=0, detail=detail)

    def request_json(
        self,
        *,
        service: str,
        name: str,
        method: str,
        url: str,
        expected_statuses: Iterable[int],
        json_body: Any | None = None,
        headers: dict[str, str] | None = None,
        validator: Callable[[HttpResponseData], str | None] | None = None,
    ) -> HttpResponseData:
        body_bytes: bytes | None = None
        request_headers = {
            "User-Agent": f"modelline-service-tracer/{VERSION}",
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        }
        if headers:
            request_headers.update(headers)
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url=url, data=body_bytes, method=method.upper(), headers=request_headers)
        started = time.perf_counter()
        raw_text = ""
        parsed_json: Any = None
        status_code: int | None = None
        response_headers: dict[str, str] = {}

        try:
            with urllib.request.urlopen(
                req,
                timeout=self.config.timeout_seconds,
                context=self.ssl_context,
            ) as response:
                status_code = response.status
                response_headers = dict(response.headers.items())
                raw_text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            response_headers = dict(exc.headers.items())
            raw_text = exc.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            duration = int((time.perf_counter() - started) * 1000)
            self.log_result(
                service=service,
                name=name,
                status=FAIL,
                method=method,
                url=url,
                http_status=None,
                duration_ms=duration,
                detail=truncate(str(exc)),
            )
            raise TraceFailure(f"{service}.{name} network failure: {exc}") from exc

        duration = int((time.perf_counter() - started) * 1000)
        expected = set(expected_statuses)
        content_type = response_headers.get("Content-Type", "")

        if raw_text:
            if "json" in content_type.lower() or raw_text[:1] in {"{", "["}:
                try:
                    parsed_json = json.loads(raw_text)
                except json.JSONDecodeError:
                    parsed_json = None

        response = HttpResponseData(
            status=status_code if status_code is not None else 0,
            headers=response_headers,
            text=raw_text,
            json_body=parsed_json,
        )

        if status_code not in expected:
            detail = truncate(f"expected {sorted(expected)}, got {status_code}, body={raw_text}")
            self.log_result(
                service=service,
                name=name,
                status=FAIL,
                method=method,
                url=url,
                http_status=status_code,
                duration_ms=duration,
                detail=detail,
            )
            raise TraceFailure(detail)

        validation_detail = None
        if validator is not None:
            try:
                validation_detail = validator(response)
            except Exception as exc:  # noqa: BLE001
                detail = truncate(str(exc))
                self.log_result(
                    service=service,
                    name=name,
                    status=FAIL,
                    method=method,
                    url=url,
                    http_status=status_code,
                    duration_ms=duration,
                    detail=detail,
                )
                raise TraceFailure(detail) from exc

        detail = validation_detail or truncate(raw_text if raw_text else "ok")
        self.log_result(
            service=service,
            name=name,
            status=PASS,
            method=method,
            url=url,
            http_status=status_code,
            duration_ms=duration,
            detail=detail,
        )
        return response

    def should_run(self, service: str) -> bool:
        if not self.config.only_services:
            return True
        return service in self.config.only_services

    def require_base(self, service: str, base_url: str | None, step_name: str, hint: str) -> str | None:
        if base_url:
            return base_url
        self.skip(service, step_name, hint)
        return None

    def bearer_headers(self) -> dict[str, str]:
        if not self.account_auth:
            raise TraceFailure("account session is not initialised")
        return {"Authorization": f"Bearer {self.account_auth['accessToken']}"}

    def ensure_account_session(self) -> None:
        if self.account_auth is not None:
            return

        service = "account"
        base_url = self.require_base(service, self.config.account_base_url, "bootstrap", "account base URL is not configured")
        if not base_url:
            raise TraceFailure("account tracer requires --account-base-url")

        suffix = random_suffix()
        self.account_email = f"trace_{suffix}@modelline.local"
        self.account_username = f"trace_{suffix}"
        self.account_password = f"TracePass1_{suffix}"

        register_response = self.request_json(
            service=service,
            name="register",
            method="POST",
            url=f"{base_url}/api/account/register",
            expected_statuses={200},
            json_body={
                "email": self.account_email,
                "username": self.account_username,
                "password": self.account_password,
            },
            validator=lambda resp: "test account created"
            if isinstance(resp.json_body, dict) and resp.json_body.get("accessToken")
            else (_ for _ in ()).throw(TraceFailure("register response does not contain tokens")),
        )
        self.account_auth = register_response.json_body

        login_response = self.request_json(
            service=service,
            name="login",
            method="POST",
            url=f"{base_url}/api/account/login",
            expected_statuses={200},
            json_body={
                "email": self.account_email,
                "password": self.account_password,
                "deviceId": "trace-runner",
                "deviceName": "deploy/run_service_traces.py",
            },
            validator=lambda resp: "credentials accepted"
            if isinstance(resp.json_body, dict) and resp.json_body.get("accessToken")
            else (_ for _ in ()).throw(TraceFailure("login response does not contain accessToken")),
        )
        self.account_auth = login_response.json_body

        me_response = self.request_json(
            service=service,
            name="me",
            method="GET",
            url=f"{base_url}/api/account/me",
            expected_statuses={200},
            headers=self.bearer_headers(),
            validator=lambda resp: "profile loaded"
            if isinstance(resp.json_body, dict) and resp.json_body.get("id")
            else (_ for _ in ()).throw(TraceFailure("me response does not contain profile id")),
        )
        self.account_profile = me_response.json_body

        settings_response = self.request_json(
            service=service,
            name="get_settings",
            method="GET",
            url=f"{base_url}/api/account/settings",
            expected_statuses={200},
            headers=self.bearer_headers(),
            validator=lambda resp: "settings loaded"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("settings response is not a JSON object")),
        )

        original_settings = settings_response.json_body if isinstance(settings_response.json_body, dict) else {}
        self.request_json(
            service=service,
            name="update_settings",
            method="PUT",
            url=f"{base_url}/api/account/settings",
            expected_statuses={200},
            headers=self.bearer_headers(),
            json_body={
                "theme": original_settings.get("theme"),
                "locale": original_settings.get("locale"),
                "notificationsEnabled": original_settings.get("notificationsEnabled"),
            },
            validator=lambda resp: "settings saved"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("update settings response is not JSON")),
        )

        refresh_response = self.request_json(
            service=service,
            name="refresh",
            method="POST",
            url=f"{base_url}/api/account/refresh",
            expected_statuses={200},
            json_body={"refreshToken": self.account_auth["refreshToken"]},
            validator=lambda resp: "token rotated"
            if isinstance(resp.json_body, dict) and resp.json_body.get("accessToken")
            else (_ for _ in ()).throw(TraceFailure("refresh response does not contain accessToken")),
        )
        self.account_auth = refresh_response.json_body

    def run_account_flow(self) -> None:
        if not self.should_run("account"):
            return
        self.ensure_account_session()
        base_url = self.config.account_base_url
        if not base_url or not self.account_auth:
            return

        self.request_json(
            service="account",
            name="logout",
            method="POST",
            url=f"{base_url}/api/account/logout",
            expected_statuses={204},
            headers=self.bearer_headers(),
            json_body={"refreshToken": self.account_auth["refreshToken"]},
            validator=lambda _resp: "session closed",
        )
        self.skip("account", "delete_account", "unsupported: no public delete-account endpoint exists in current contract")

    def run_infra_flow(self) -> None:
        if not self.should_run("infra"):
            return
        base_url = self.require_base(
            "infra",
            self.config.infra_base_url,
            "health",
            "infra base URL is not configured; pass --infra-base-url",
        )
        if not base_url:
            return

        self.request_json(
            service="infra",
            name="health",
            method="GET",
            url=f"{base_url}/health",
            expected_statuses={200},
            validator=lambda _resp: "ingress online",
        )
        self.request_json(
            service="infra",
            name="ready",
            method="GET",
            url=f"{base_url}/health/ready",
            expected_statuses={200},
            validator=self.validate_infra_ready,
        )

    def run_data_flow(self) -> None:
        if not self.should_run("data"):
            return
        base_url = self.require_base(
            "data",
            self.config.data_base_url,
            "health",
            "data base URL is not configured; pass --data-base-url or skip data",
        )
        if not base_url:
            return

        self.request_json(
            service="data",
            name="health",
            method="GET",
            url=f"{base_url}/health",
            expected_statuses={200},
            validator=lambda resp: f"{resp.json_body.get('service', 'service')} online"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("health response is not JSON")),
        )

    def run_analytics_flow(self) -> None:
        if not self.should_run("analytics"):
            return
        base_url = self.require_base(
            "analytics",
            self.config.analytics_base_url,
            "health",
            "analytics base URL is not configured; pass --analytics-base-url or skip analytics",
        )
        if not base_url:
            return

        self.request_json(
            service="analytics",
            name="health",
            method="GET",
            url=f"{base_url}/health",
            expected_statuses={200},
            validator=lambda resp: "analytics online"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("analytics health response is not JSON")),
        )
        self.request_json(
            service="analytics",
            name="registry",
            method="GET",
            url=f"{base_url}/registry",
            expected_statuses={200},
            validator=lambda resp: f"{len(resp.json_body.get('models', []))} models"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("analytics registry response is not JSON")),
        )

    def run_gateway_flow(self) -> None:
        if not self.should_run("gateway"):
            return
        base_url = self.require_base(
            "gateway",
            self.config.gateway_base_url,
            "health",
            "gateway base URL is not configured; pass --gateway-base-url",
        )
        if not base_url:
            return

        self.request_json(
            service="gateway",
            name="health",
            method="GET",
            url=f"{base_url}/health",
            expected_statuses={200},
            validator=lambda _resp: "gateway online",
        )
        self.request_json(
            service="gateway",
            name="market_config",
            method="GET",
            url=f"{base_url}/api/v1/market/config",
            expected_statuses={200},
            validator=lambda resp: f"{len(resp.json_body.get('timeframes', []))} timeframes"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("market config response is not JSON")),
        )
        chart_url = (
            f"{base_url}/api/v1/market/chart"
            f"?symbol={urllib.parse.quote(self.config.target_symbol)}"
            f"&timeframe={urllib.parse.quote(self.config.timeframe)}"
            f"&limit={self.config.candle_limit}"
        )
        self.request_json(
            service="gateway",
            name="market_chart",
            method="GET",
            url=chart_url,
            expected_statuses={200},
            validator=self.validate_chart,
        )

    def run_news_flow(self) -> None:
        if not self.should_run("news"):
            return
        direct_base = self.config.news_base_url
        gateway_base = self.config.gateway_base_url

        if direct_base:
            url = f"{direct_base}/api/news?page=1&pageSize=5"
            detail_prefix = "direct"
        elif gateway_base:
            url = f"{gateway_base}/api/news?page=1&pageSize=5"
            detail_prefix = "via-gateway"
        else:
            self.skip("news", "list", "neither --news-base-url nor --gateway-base-url is configured")
            return

        self.request_json(
            service="news",
            name="list",
            method="GET",
            url=url,
            expected_statuses={200},
            validator=lambda resp: self.validate_list_response(resp, detail_prefix, "news"),
        )

    def run_notification_flow(self) -> None:
        if not self.should_run("notification"):
            return
        self.ensure_account_session()
        direct_base = self.config.notification_base_url
        gateway_base = self.config.gateway_base_url
        headers = self.bearer_headers()

        if direct_base:
            unread_url = f"{direct_base}/api/notifications/unread-count"
            settings_url = f"{direct_base}/api/notification-settings"
            list_url = f"{direct_base}/api/notifications?page=1&pageSize=5"
            mode = "direct"
        elif gateway_base:
            unread_url = f"{gateway_base}/api/notifications/unread-count"
            settings_url = f"{gateway_base}/api/notification-settings"
            list_url = f"{gateway_base}/api/notifications?page=1&pageSize=5"
            mode = "via-gateway"
        else:
            self.skip("notification", "bootstrap", "notification tracing requires gateway or direct notification URL")
            return

        self.request_json(
            service="notification",
            name="unread_count",
            method="GET",
            url=unread_url,
            expected_statuses={200},
            headers=headers,
            validator=lambda resp: f"{resp.json_body.get('unread')} unread"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("unread-count response is not JSON")),
        )
        settings_response = self.request_json(
            service="notification",
            name="get_settings",
            method="GET",
            url=settings_url,
            expected_statuses={200},
            headers=headers,
            validator=lambda resp: f"threshold {resp.json_body.get('priceThresholdPct')}%"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("notification settings response is not JSON")),
        )
        self.notification_settings = settings_response.json_body if isinstance(settings_response.json_body, dict) else {}
        self.request_json(
            service="notification",
            name="update_settings",
            method="PUT",
            url=settings_url,
            expected_statuses={200},
            headers=headers,
            json_body={
                "enableReply": self.notification_settings.get("enableReply"),
                "enableNews": self.notification_settings.get("enableNews"),
                "enablePrice": self.notification_settings.get("enablePrice"),
                "priceThresholdPct": self.notification_settings.get("priceThresholdPct"),
            },
            validator=lambda resp: "settings saved"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("notification update response is not JSON")),
        )
        self.request_json(
            service="notification",
            name="list",
            method="GET",
            url=list_url,
            expected_statuses={200},
            headers=headers,
            validator=lambda resp: f"{resp.json_body.get('total')} total, {resp.json_body.get('unread')} unread"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("notification list response is not JSON")),
        )

    def run_social_flow(self) -> None:
        if not self.should_run("social"):
            return
        self.ensure_account_session()
        direct_base = self.config.social_base_url
        gateway_base = self.config.gateway_base_url
        headers = self.bearer_headers()

        if direct_base:
            comments_base = f"{direct_base}/api/social/comments"
            favorites_base = f"{direct_base}/api/social/favorites"
            mode = "direct"
        elif gateway_base:
            comments_base = f"{gateway_base}/api/social/comments"
            favorites_base = f"{gateway_base}/api/social/favorites"
            mode = "via-gateway"
        else:
            self.skip("social", "bootstrap", "social tracing requires gateway or direct social URL")
            return

        query = urllib.parse.urlencode(
            {
                "targetType": self.config.comment_target_type,
                "targetId": self.config.comment_target_id,
                "page": 1,
                "pageSize": 5,
            }
        )
        self.request_json(
            service="social",
            name="list_comments",
            method="GET",
            url=f"{comments_base}?{query}",
            expected_statuses={200},
            validator=lambda resp: f"{resp.json_body.get('total')} comments"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("comment list response is not JSON")),
        )

        favorite_symbol = self.config.target_symbol
        self.request_json(
            service="social",
            name="favorite_add",
            method="PUT",
            url=f"{favorites_base}/{urllib.parse.quote(favorite_symbol)}",
            expected_statuses={204},
            headers=headers,
            validator=lambda _resp: "favorite saved",
        )
        self.request_json(
            service="social",
            name="favorite_list",
            method="GET",
            url=f"{favorites_base}",
            expected_statuses={200},
            headers=headers,
            validator=lambda resp: f"{len(resp.json_body.get('symbols', []))} symbols"
            if isinstance(resp.json_body, dict) and favorite_symbol in resp.json_body.get("symbols", [])
            else (_ for _ in ()).throw(TraceFailure(f"favorite symbol {favorite_symbol} not present in list")),
        )

        body = {
            "targetType": self.config.comment_target_type,
            "targetId": self.config.comment_target_id,
            "body": f"trace comment {uuid.uuid4().hex[:12]}",
        }
        comment_response = self.request_json(
            service="social",
            name="comment_create",
            method="POST",
            url=comments_base,
            expected_statuses={200},
            headers=headers,
            json_body=body,
            validator=lambda resp: "comment created"
            if isinstance(resp.json_body, dict) and resp.json_body.get("id")
            else (_ for _ in ()).throw(TraceFailure("comment create response does not contain id")),
        )
        self.created_comment_id = str(comment_response.json_body["id"])

        self.request_json(
            service="social",
            name="comment_like",
            method="POST",
            url=f"{comments_base}/{self.created_comment_id}/like",
            expected_statuses={204},
            headers=headers,
            validator=lambda _resp: "like added",
        )
        self.request_json(
            service="social",
            name="comment_unlike",
            method="DELETE",
            url=f"{comments_base}/{self.created_comment_id}/like",
            expected_statuses={204},
            headers=headers,
            validator=lambda _resp: "like removed",
        )
        self.request_json(
            service="social",
            name="comment_delete",
            method="DELETE",
            url=f"{comments_base}/{self.created_comment_id}",
            expected_statuses={204},
            headers=headers,
            validator=lambda _resp: "comment removed",
        )
        self.created_comment_id = None

        self.request_json(
            service="social",
            name="favorite_remove",
            method="DELETE",
            url=f"{favorites_base}/{urllib.parse.quote(favorite_symbol)}",
            expected_statuses={204},
            headers=headers,
            validator=lambda _resp: "favorite removed",
        )

    def run_admin_flow(self) -> None:
        if not self.should_run("admin"):
            return
        base_url = self.require_base(
            "admin",
            self.config.admin_base_url,
            "health",
            "admin base URL is not configured; pass --admin-base-url to trace the admin head",
        )
        if not base_url:
            return

        self.request_json(
            service="admin",
            name="page",
            method="GET",
            url=base_url,
            expected_statuses={200},
            headers={"Accept": "text/html"},
            validator=lambda resp: "page rendered"
            if "<html" in resp.text.lower() or "<!doctype html" in resp.text.lower()
            else (_ for _ in ()).throw(TraceFailure("admin page did not return HTML")),
        )
        self.request_json(
            service="admin",
            name="api_health",
            method="GET",
            url=f"{base_url}/api/health",
            expected_statuses={200},
            validator=lambda resp: "backend probe ok"
            if isinstance(resp.json_body, dict)
            else (_ for _ in ()).throw(TraceFailure("admin api health response is not JSON")),
        )

    def validate_infra_ready(self, resp: HttpResponseData) -> str:
        if not isinstance(resp.json_body, dict):
            raise TraceFailure("readiness response is not JSON")
        checks = resp.json_body.get("checks")
        if not isinstance(checks, dict):
            raise TraceFailure("readiness response does not contain checks")
        healthy = [name for name, data in checks.items() if isinstance(data, dict) and data.get("status") == "Healthy"]
        if not healthy:
            raise TraceFailure("readiness response did not report healthy checks")
        return f"ready: {', '.join(healthy[:3])}"

    def validate_chart(self, resp: HttpResponseData) -> str:
        if not isinstance(resp.json_body, dict):
            raise TraceFailure("market chart response is not JSON")
        candles = resp.json_body.get("candles")
        if not isinstance(candles, list):
            raise TraceFailure("market chart response does not contain a candles list")
        if not candles:
            raise TraceFailure("market chart returned zero candles")
        status = resp.json_body.get("status") or "ok"
        return f"{len(candles)} candles, {status}"

    def validate_list_response(self, resp: HttpResponseData, mode: str, list_key: str) -> str:
        if not isinstance(resp.json_body, dict):
            raise TraceFailure(f"{list_key} response is not JSON")
        if "items" in resp.json_body and isinstance(resp.json_body["items"], list):
            return f"{len(resp.json_body['items'])} items"
        if "articles" in resp.json_body and isinstance(resp.json_body["articles"], list):
            return f"{len(resp.json_body['articles'])} articles"
        return f"response keys: {','.join(sorted(resp.json_body.keys())[:3])}"

    def finalise(self) -> int:
        total = len(self.results)
        passed = sum(1 for item in self.results if item.status == PASS)
        failed = sum(1 for item in self.results if item.status == FAIL)
        skipped = sum(1 for item in self.results if item.status == SKIP)

        print()
        print(self.paint("== Summary ==", "bold", "white"))
        print(
            f"total {total}   "
            f"{self.paint(f'pass {passed}', 'green', 'bold')}   "
            f"{self.paint(f'fail {failed}', 'red', 'bold')}   "
            f"{self.paint(f'skip {skipped}', 'yellow', 'bold')}"
        )

        if self.config.json_report:
            payload = {
                "version": VERSION,
                "backendHost": self.config.backend_host,
                "summary": {
                    "total": total,
                    "pass": passed,
                    "fail": failed,
                    "skip": skipped,
                },
                "results": [asdict(item) for item in self.results],
            }
            with open(self.config.json_report, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            print(f"JSON report written to {self.config.json_report}")

        if failed > 0:
            return 1
        if self.config.strict and skipped > 0:
            return 2
        return 0


def parse_services(raw: str | None) -> set[str]:
    if raw is None or not raw.strip():
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def build_config() -> RunnerConfig:
    admin_env = read_simple_env_file(Path(__file__).resolve().parents[1] / "microservice_admin" / ".env")

    parser = argparse.ArgumentParser(
        description="Console tracer for ModelLine runtime services.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--backend-host")
    parser.add_argument("--timeout-seconds", type=float, default=float(env("MODELLINE_TRACER_TIMEOUT", "10") or "10"))
    parser.add_argument("--insecure-https", action="store_true")
    parser.add_argument("--color", choices=["auto", "always", "never"], default=env("MODELLINE_TRACER_COLOR", "auto"))
    parser.add_argument("--verbose-output", action="store_true", default=(env("MODELLINE_TRACER_VERBOSE", "0") == "1"))
    parser.add_argument("--infra-base-url")
    parser.add_argument("--admin-base-url")
    parser.add_argument("--account-base-url")
    parser.add_argument("--data-base-url")
    parser.add_argument("--analytics-base-url")
    parser.add_argument("--gateway-base-url")
    parser.add_argument("--news-base-url")
    parser.add_argument("--notification-base-url")
    parser.add_argument("--social-base-url")
    parser.add_argument("--target-symbol", default=env("MODELLINE_TRACER_SYMBOL", "BTCUSDT"))
    parser.add_argument("--timeframe", default=env("MODELLINE_TRACER_TIMEFRAME", "1m"))
    parser.add_argument("--candle-limit", type=int, default=int(env("MODELLINE_TRACER_LIMIT", "100") or "100"))
    parser.add_argument("--comment-target-type", default=env("MODELLINE_TRACER_COMMENT_TARGET_TYPE", "asset"))
    parser.add_argument("--comment-target-id", default=env("MODELLINE_TRACER_COMMENT_TARGET_ID", "BTCUSDT"))
    parser.add_argument("--only", default=env("MODELLINE_TRACER_ONLY"))
    parser.add_argument("--strict", action="store_true", default=(env("MODELLINE_TRACER_STRICT", "0") == "1"))
    parser.add_argument("--json-report", default=env("MODELLINE_TRACER_JSON_REPORT"))
    args = parser.parse_args()

    host = first_non_empty(
        args.backend_host,
        env("MODELLINE_TRACER_BACKEND_HOST"),
        admin_env.get("ONLINE_BACKEND_HOST"),
        admin_env.get("BACKEND_CONNECTION_TARGET"),
        extract_host_from_url(admin_env.get("ADMIN_BACKEND_BASE_URL")),
        extract_host_from_url(admin_env.get("ACCOUNT_URL")),
        extract_host_from_url(admin_env.get("GATEWAY_URL")),
        "127.0.0.1",
    )
    assert host is not None

    default_account = first_non_empty(
        args.account_base_url,
        env("MODELLINE_TRACER_ACCOUNT_URL"),
        admin_env.get("ACCOUNT_URL"),
        f"http://{host}:7510",
    )
    default_gateway = first_non_empty(
        args.gateway_base_url,
        env("MODELLINE_TRACER_GATEWAY_URL"),
        admin_env.get("GATEWAY_URL"),
        f"http://{host}:7520",
    )
    default_data = first_non_empty(args.data_base_url, env("MODELLINE_TRACER_DATA_URL"), f"http://{host}:8100")
    default_analytics = first_non_empty(args.analytics_base_url, env("MODELLINE_TRACER_ANALYTICS_URL"), f"http://{host}:8000")
    default_infra = first_non_empty(
        args.infra_base_url,
        env("MODELLINE_TRACER_INFRA_URL"),
        admin_env.get("ADMIN_BACKEND_BASE_URL"),
        f"https://{host}:8443",
    )
    default_admin = f"http://{host}:8501/admin" if host in {"127.0.0.1", "localhost"} else None
    insecure_https = args.insecure_https or env("MODELLINE_TRACER_INSECURE", "0") == "1" or admin_env.get("ADMIN_BACKEND_TLS_INSECURE", "0") == "1"

    return RunnerConfig(
        backend_host=host,
        timeout_seconds=args.timeout_seconds,
        insecure_https=insecure_https,
        color_mode=args.color,
        verbose_output=args.verbose_output,
        infra_base_url=normalize_service_base(default_infra, default_scheme="https"),
        admin_base_url=normalize_service_base(first_non_empty(args.admin_base_url, env("MODELLINE_TRACER_ADMIN_URL"), default_admin)),
        account_base_url=normalize_service_base(default_account),
        data_base_url=normalize_service_base(default_data),
        analytics_base_url=normalize_service_base(default_analytics),
        gateway_base_url=normalize_service_base(default_gateway),
        news_base_url=normalize_service_base(first_non_empty(args.news_base_url, env("MODELLINE_TRACER_NEWS_URL"))),
        notification_base_url=normalize_service_base(first_non_empty(args.notification_base_url, env("MODELLINE_TRACER_NOTIFICATION_URL"))),
        social_base_url=normalize_service_base(first_non_empty(args.social_base_url, env("MODELLINE_TRACER_SOCIAL_URL"))),
        target_symbol=args.target_symbol.strip().upper(),
        timeframe=args.timeframe.strip(),
        candle_limit=max(1, min(args.candle_limit, 500)),
        comment_target_type=args.comment_target_type.strip(),
        comment_target_id=args.comment_target_id.strip(),
        only_services=parse_services(args.only),
        strict=args.strict,
        json_report=normalize_base(args.json_report) if args.json_report else args.json_report,
    )


def main() -> int:
    config = build_config()
    runner = TraceRunner(config)
    runner.print_header()

    try:
        runner.run_infra_flow()
        runner.run_data_flow()
        runner.run_analytics_flow()
        runner.run_gateway_flow()
        runner.run_news_flow()
        runner.run_notification_flow()
        runner.run_social_flow()
        runner.run_account_flow()
        runner.run_admin_flow()
    except TraceFailure:
        pass

    return runner.finalise()


if __name__ == "__main__":
    sys.exit(main())