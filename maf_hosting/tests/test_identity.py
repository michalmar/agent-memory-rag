from __future__ import annotations

import asyncio
import os
import unittest
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

from maf_hosting.identity import (
    Agent365IdentityMiddleware,
    configure_observability_identity,
    install_agent365_identity_middleware,
)


class ObservabilityIdentityTests(unittest.TestCase):
    def test_uses_deployment_tenant_fallback(self) -> None:
        with patch.dict(
            os.environ,
            {"ENTRA_TENANT_ID": "deployment-tenant"},
            clear=True,
        ):
            identity = configure_observability_identity()
            self.assertEqual(
                os.environ["FOUNDRY_AGENT_TENANT_ID"],
                "deployment-tenant",
            )

        self.assertEqual(identity, ("deployment-tenant", None))

    def test_preserves_platform_tenant(self) -> None:
        with patch.dict(
            os.environ,
            {
                "FOUNDRY_AGENT_TENANT_ID": "platform-tenant",
                "ENTRA_TENANT_ID": "deployment-tenant",
            },
            clear=True,
        ):
            identity = configure_observability_identity()

        self.assertEqual(identity, ("platform-tenant", None))

    def test_requires_tenant(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ENTRA_TENANT_ID is required"):
                configure_observability_identity()

    def test_requires_agent_id_when_hosted(self) -> None:
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
                configure_observability_identity()

    def test_returns_published_agent_identity(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENTRA_TENANT_ID": "deployment-tenant",
                "FOUNDRY_AGENT_INSTANCE_CLIENT_ID": "published-agent",
                "FOUNDRY_HOSTING_ENVIRONMENT": "hosted",
            },
            clear=True,
        ):
            identity = configure_observability_identity()

        self.assertEqual(identity, ("deployment-tenant", "published-agent"))


class Agent365IdentityMiddlewareTests(unittest.TestCase):
    def test_makes_invoke_agent_span_eligible(self) -> None:
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

        middleware = Agent365IdentityMiddleware(
            app,
            tenant_id="deployment-tenant",
            agent_id="published-agent",
        )

        async def invoke() -> None:
            async def receive():
                return {"type": "http.disconnect"}

            async def send(message) -> None:
                return None

            await middleware({"type": "http"}, receive, send)

        asyncio.run(invoke())
        groups = filter_and_partition_by_identity(exporter.get_finished_spans())

        self.assertEqual(
            list(groups),
            [("deployment-tenant", "published-agent")],
        )
        self.assertEqual(len(groups[("deployment-tenant", "published-agent")]), 1)

    def test_wraps_only_create_response_route(self) -> None:
        async def app(scope, receive, send) -> None:
            return None

        create_route = SimpleNamespace(name="create_response", app=app)
        readiness_route = SimpleNamespace(name="readiness", app=app)
        server = SimpleNamespace(routes=[create_route, readiness_route])

        install_agent365_identity_middleware(
            server,
            tenant_id="deployment-tenant",
            agent_id="published-agent",
        )

        self.assertIsInstance(create_route.app, Agent365IdentityMiddleware)
        self.assertIs(readiness_route.app, app)

    def test_requires_exactly_one_create_response_route(self) -> None:
        async def app(scope, receive, send) -> None:
            return None

        servers = [
            SimpleNamespace(routes=[]),
            SimpleNamespace(
                routes=[
                    SimpleNamespace(name="create_response", app=app),
                    SimpleNamespace(name="create_response", app=app),
                ]
            ),
        ]
        for server in servers:
            with self.subTest(route_count=len(server.routes)):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "exactly one create_response",
                ):
                    install_agent365_identity_middleware(
                        server,
                        tenant_id="deployment-tenant",
                        agent_id="published-agent",
                    )


if __name__ == "__main__":
    unittest.main()
