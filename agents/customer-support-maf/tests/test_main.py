from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from microsoft.opentelemetry.a365.core.exporters.span_processor import (
    A365SpanProcessor,
)
from microsoft.opentelemetry.a365.core.exporters.utils import (
    filter_and_partition_by_identity,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

SOURCE_DIR = Path(__file__).resolve().parents[1] / "src" / "customer-support-maf"
sys.path.insert(0, str(SOURCE_DIR))

import main as hosted_main


class HostedMcpConfigurationTests(unittest.TestCase):
    def test_application_tool_includes_endpoint_and_connection(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_TOOL_GATEWAY_URL": "https://frontend.example/api/",
                "APP_TOOLS_CONNECTION_ID": "application-tools",
            },
        ):
            tool = hosted_main._build_application_tools()

        self.assertEqual(
            tool["server_url"],
            "https://frontend.example/api/mcp/",
        )
        self.assertEqual(tool["project_connection_id"], "application-tools")
        self.assertEqual(tool["allowed_tools"], ["get_order_status"])
        self.assertEqual(tool["require_approval"], "never")

    def test_observability_identity_uses_deployment_tenant_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {"ENTRA_TENANT_ID": "deployment-tenant"},
            clear=True,
        ):
            hosted_main._configure_observability_identity()

            self.assertEqual(
                os.environ["FOUNDRY_AGENT_TENANT_ID"],
                "deployment-tenant",
            )

    def test_observability_identity_preserves_platform_tenant(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FOUNDRY_AGENT_TENANT_ID": "platform-tenant",
                "ENTRA_TENANT_ID": "deployment-tenant",
            },
            clear=True,
        ):
            hosted_main._configure_observability_identity()

            self.assertEqual(
                os.environ["FOUNDRY_AGENT_TENANT_ID"],
                "platform-tenant",
            )

    def test_observability_identity_requires_tenant(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ENTRA_TENANT_ID is required"):
                hosted_main._configure_observability_identity()

    def test_observability_identity_requires_agent_id_when_hosted(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENTRA_TENANT_ID": "deployment-tenant",
                "FOUNDRY_HOSTING_ENVIRONMENT": "hosted",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "FOUNDRY_AGENT_INSTANCE_CLIENT_ID is required",
            ):
                hosted_main._configure_observability_identity()

    def test_observability_identity_returns_published_agent_identity(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENTRA_TENANT_ID": "deployment-tenant",
                "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "published-agent",
                "FOUNDRY_HOSTING_ENVIRONMENT": "hosted",
            },
            clear=True,
        ):
            identity = hosted_main._configure_observability_identity()

        self.assertEqual(identity, ("deployment-tenant", "published-agent"))

    def test_agent365_identity_middleware_makes_invoke_agent_eligible(self) -> None:
        provider = TracerProvider()
        self.addCleanup(provider.shutdown)
        provider.add_span_processor(A365SpanProcessor())
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer(__name__)

        async def app(scope, receive, send) -> None:
            with tracer.start_as_current_span(
                "invoke_agent",
                attributes={"gen_ai.operation.name": "invoke_agent"},
            ):
                pass

        middleware = hosted_main._Agent365IdentityMiddleware(
            app,
            tenant_id="deployment-tenant",
            agent_id="published-agent",
        )

        async def invoke() -> None:
            async def receive():
                return {"type": "http.disconnect"}

            async def send(message) -> None:
                return None

            await middleware(
                {"type": "http"},
                receive,
                send,
            )

        asyncio.run(invoke())
        spans = exporter.get_finished_spans()
        groups = filter_and_partition_by_identity(spans)

        self.assertEqual(
            list(groups),
            [("deployment-tenant", "published-agent")],
        )
        self.assertEqual(len(groups[("deployment-tenant", "published-agent")]), 1)

    def test_agent365_identity_middleware_wraps_create_route(self) -> None:
        class FakeAgent:
            context_providers = []

        with patch.dict(os.environ, {}, clear=True):
            server = hosted_main.ResponsesHostServer(
                FakeAgent(),
                configure_observability=None,
            )
        create_route = next(
            route for route in server.routes if route.name == "create_response"
        )
        readiness_route = next(
            route for route in server.routes if route.name == "readiness"
        )
        original_readiness_app = readiness_route.app

        hosted_main._install_agent365_identity_middleware(
            server,
            tenant_id="deployment-tenant",
            agent_id="published-agent",
        )

        self.assertIsInstance(
            create_route.app,
            hosted_main._Agent365IdentityMiddleware,
        )
        self.assertIs(readiness_route.app, original_readiness_app)

    def test_agent365_identity_middleware_requires_create_route(self) -> None:
        server = SimpleNamespace(routes=[])

        with self.assertRaisesRegex(RuntimeError, "exactly one create_response"):
            hosted_main._install_agent365_identity_middleware(
                server,
                tenant_id="deployment-tenant",
                agent_id="published-agent",
            )


if __name__ == "__main__":
    unittest.main()
