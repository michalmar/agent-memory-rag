from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from maf_hosting import runtime


class HostedAgentRuntimeTests(unittest.TestCase):
    def _hosting_module(self, server_factory: Mock) -> ModuleType:
        module = ModuleType("agent_framework_foundry_hosting")
        module.ResponsesHostServer = server_factory
        return module

    def test_builds_enriches_and_runs_hosted_agent(self) -> None:
        agent = object()
        build_agent = Mock(return_value=agent)
        server = SimpleNamespace(run=Mock())
        server_factory = Mock(return_value=server)

        with (
            patch.dict(
                sys.modules,
                {
                    "agent_framework_foundry_hosting": self._hosting_module(
                        server_factory
                    )
                },
            ),
            patch.object(
                runtime,
                "configure_observability_identity",
                return_value=("deployment-tenant", "published-agent"),
            ),
            patch.object(
                runtime,
                "install_agent365_identity_middleware",
            ) as install_middleware,
        ):
            runtime.run_hosted_agent(build_agent)

        build_agent.assert_called_once_with()
        server_factory.assert_called_once_with(agent)
        install_middleware.assert_called_once_with(
            server,
            tenant_id="deployment-tenant",
            agent_id="published-agent",
        )
        server.run.assert_called_once_with()

    def test_runs_without_middleware_before_publication(self) -> None:
        server = SimpleNamespace(run=Mock())
        server_factory = Mock(return_value=server)

        with (
            patch.dict(
                sys.modules,
                {
                    "agent_framework_foundry_hosting": self._hosting_module(
                        server_factory
                    )
                },
            ),
            patch.object(
                runtime,
                "configure_observability_identity",
                return_value=("deployment-tenant", None),
            ),
            patch.object(
                runtime,
                "install_agent365_identity_middleware",
            ) as install_middleware,
        ):
            runtime.run_hosted_agent(Mock(return_value=object()))

        install_middleware.assert_not_called()
        server.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
