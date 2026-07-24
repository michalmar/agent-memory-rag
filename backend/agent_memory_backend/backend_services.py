"""Construct, start, stop, and report health for backend services."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_contracts import (
    AgentType,
    DIRECTIVE_RAG_PROMPT_VERSION,
    PROMPT_VERSION,
)

from .agent_runtime_contracts import AgentRuntime
from .agent_tools import ToolExecutor
from .azure_clients import close_azure_clients
from .config import Settings
from .conversation_coordinator import ConversationCoordinator
from .conversation_history import ConversationHistoryStore
from .conversation_memory import ConversationMemoryStore
from .conversation_registry import ConversationRegistry
from .directive_artifacts import DirectiveArtifactRepository
from .directive_catalog import DirectiveCatalogRepository
from .directive_mandates import DirectiveMandateRepository
from .directive_search import DirectiveSearchRepository
from .directive_tools import DirectiveToolExecutor
from .foundry_hosted_maf_runtime import FoundryHostedMafRuntime
from .foundry_iq_health import FoundryIqHealthProbe
from .foundry_prompt_runtime import FoundryPromptRuntime
from .health import run_readiness_check
from .memory_agent import MemoryAgent
from .mock_agent_runtime import MockAgentRuntime
from .profile_agent import ProfileAgent
from .telemetry import span
from .user_profile_memory import UserProfileMemoryStore

logger = logging.getLogger("backend_services")
_SUPPORT_AGENT_TYPES = (
    AgentType.FOUNDRY_PROMPT,
    AgentType.AGENT_FRAMEWORK,
)
_RUNTIME_RETRY_INITIAL_SECONDS = 5.0
_RUNTIME_RETRY_MAX_SECONDS = 300.0


def visible_agent_types(settings: Settings) -> tuple[AgentType, ...]:
    if settings.directive_agent_visible:
        return (*_SUPPORT_AGENT_TYPES, AgentType.DIRECTIVE_RAG)
    return _SUPPORT_AGENT_TYPES


class ManagedComponent(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class BackendServices:
    conversation_registry: ConversationRegistry
    history_store: ConversationHistoryStore
    profile_store: UserProfileMemoryStore
    memory_store: ConversationMemoryStore
    foundry_iq_health: FoundryIqHealthProbe
    memory_agent: MemoryAgent
    profile_agent: ProfileAgent
    tool_executor: ToolExecutor
    directive_catalog: DirectiveCatalogRepository
    directive_artifacts: DirectiveArtifactRepository
    directive_search: DirectiveSearchRepository
    directive_mandates: DirectiveMandateRepository
    directive_tool_executor: DirectiveToolExecutor
    tool_executors: dict[AgentType, Any]
    runtime_registry: dict[AgentType, AgentRuntime]
    conversation_coordinator: ConversationCoordinator
    _runtime_candidates: dict[AgentType, AgentRuntime] = field(
        default_factory=dict,
        init=False,
    )
    _runtime_retry_tasks: dict[AgentType, asyncio.Task[None]] = field(
        default_factory=dict,
        init=False,
    )
    _closing: bool = field(default=False, init=False)

    @classmethod
    def build(cls) -> BackendServices:
        conversation_registry = ConversationRegistry()
        history_store = ConversationHistoryStore()
        profile_store = UserProfileMemoryStore()
        memory_store = ConversationMemoryStore()
        directive_catalog = DirectiveCatalogRepository()
        directive_artifacts = DirectiveArtifactRepository()
        directive_search = DirectiveSearchRepository()
        directive_mandates = DirectiveMandateRepository()
        tool_executor = ToolExecutor(memory_store, profile_store)
        directive_tool_executor = DirectiveToolExecutor(
            directive_catalog,
            directive_artifacts,
            directive_search,
            directive_mandates,
        )
        runtime_registry: dict[AgentType, AgentRuntime] = {}
        return cls(
            conversation_registry=conversation_registry,
            history_store=history_store,
            profile_store=profile_store,
            memory_store=memory_store,
            foundry_iq_health=FoundryIqHealthProbe(),
            memory_agent=MemoryAgent(),
            profile_agent=ProfileAgent(),
            tool_executor=tool_executor,
            directive_catalog=directive_catalog,
            directive_artifacts=directive_artifacts,
            directive_search=directive_search,
            directive_mandates=directive_mandates,
            directive_tool_executor=directive_tool_executor,
            tool_executors={
                AgentType.AGENT_FRAMEWORK: tool_executor,
                AgentType.DIRECTIVE_RAG: directive_tool_executor,
            },
            runtime_registry=runtime_registry,
            conversation_coordinator=ConversationCoordinator(
                conversation_registry,
                history_store,
                memory_store,
                runtime_registry,
            ),
        )

    @property
    def managed_components(
        self,
    ) -> tuple[tuple[str, ManagedComponent], ...]:
        return (
            ("cosmos_history", self.history_store),
            ("cosmos_profile", self.profile_store),
            ("cosmos_memory", self.memory_store),
            ("foundry_iq", self.foundry_iq_health),
            ("directive_catalog", self.directive_catalog),
            ("directive_artifacts", self.directive_artifacts),
            ("directive_search", self.directive_search),
            ("directive_mandates", self.directive_mandates),
        )

    async def start(self, settings: Settings) -> None:
        self._closing = False
        await asyncio.gather(
            *(
                self._initialize_component(name, component)
                for name, component in self.managed_components
            )
        )

        runtimes = self._runtime_components(settings)
        self._runtime_candidates = runtimes
        await asyncio.gather(
            *(
                self._initialize_runtime(agent_type, runtime)
                for agent_type, runtime in runtimes.items()
            )
        )

        logger.info(
            "[startup] backend initialized "
            "(history=%s profile=%s memory=%s runtimes=%s)",
            self.history_store.enabled,
            self.profile_store.enabled,
            self.memory_store.enabled,
            ",".join(runtime.value for runtime in self.runtime_registry),
        )

    def _runtime_components(
        self,
        settings: Settings,
    ) -> dict[AgentType, AgentRuntime]:
        if settings.resolve_llm_mode() == "mock":
            agent_types = list(_SUPPORT_AGENT_TYPES)
            if settings.directive_agent_enabled:
                agent_types.append(AgentType.DIRECTIVE_RAG)
            return {
                agent_type: MockAgentRuntime(
                    agent_type,
                    (
                        self.directive_tool_executor
                        if agent_type is AgentType.DIRECTIVE_RAG
                        else self.tool_executor
                    ),
                    release_id=(
                        settings.directive_agent_release_id
                        if agent_type is AgentType.DIRECTIVE_RAG
                        else settings.agent_release_id
                    ),
                    prompt_version=(
                        DIRECTIVE_RAG_PROMPT_VERSION
                        if agent_type is AgentType.DIRECTIVE_RAG
                        else None
                    ),
                )
                for agent_type in agent_types
            }

        runtimes: dict[AgentType, AgentRuntime] = {}
        if settings.foundry_prompt_enabled:
            runtimes[AgentType.FOUNDRY_PROMPT] = FoundryPromptRuntime()
        if settings.foundry_hosted_enabled:
            try:
                runtimes[AgentType.AGENT_FRAMEWORK] = FoundryHostedMafRuntime(
                    agent_type=AgentType.AGENT_FRAMEWORK,
                    project_endpoint=settings.foundry_project_endpoint,
                    physical_agent_name=settings.foundry_hosted_agent_name,
                    physical_agent_endpoint=(
                        settings.foundry_hosted_agent_endpoint
                    ),
                    release_id=settings.agent_release_id,
                    prompt_version=PROMPT_VERSION,
                    request_timeout_seconds=(
                        settings.agent_request_timeout_seconds
                    ),
                )
            except ValueError:
                logger.exception(
                    "[startup] support Hosted runtime configuration is invalid"
                )
        if settings.directive_agent_enabled:
            if settings.directive_agent_configured:
                try:
                    runtimes[AgentType.DIRECTIVE_RAG] = (
                        FoundryHostedMafRuntime(
                            agent_type=AgentType.DIRECTIVE_RAG,
                            project_endpoint=settings.foundry_project_endpoint,
                            physical_agent_name=(
                                settings.directive_foundry_agent_name
                            ),
                            physical_agent_endpoint=(
                                settings.directive_foundry_agent_endpoint
                            ),
                            release_id=settings.directive_agent_release_id,
                            prompt_version=DIRECTIVE_RAG_PROMPT_VERSION,
                            request_timeout_seconds=(
                                settings.agent_request_timeout_seconds
                            ),
                            progress_heartbeat_seconds=(
                                settings.directive_progress_heartbeat_seconds
                            ),
                        )
                    )
                except ValueError:
                    logger.exception(
                        "[startup] directive Hosted runtime configuration "
                        "is invalid"
                    )
            else:
                logger.error(
                    "[startup] directive runtime enabled but not configured"
                )
        return runtimes

    async def close(self) -> None:
        self._closing = True
        retry_tasks = tuple(self._runtime_retry_tasks.values())
        for task in retry_tasks:
            task.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)
        self._runtime_retry_tasks.clear()

        runtimes = {
            id(runtime): (f"runtime_{agent_type.value}", runtime)
            for agent_type, runtime in self._runtime_candidates.items()
        }
        for agent_type, runtime in self.runtime_registry.items():
            runtimes.setdefault(
                id(runtime),
                (f"runtime_{agent_type.value}", runtime),
            )
        await asyncio.gather(
            *(
                self._close_component(name, runtime)
                for name, runtime in runtimes.values()
            ),
            *(
                self._close_component(name, component)
                for name, component in reversed(self.managed_components)
            ),
        )
        self.runtime_registry.clear()
        self._runtime_candidates.clear()
        await close_azure_clients()
        self.conversation_registry.close()

    async def readiness(self, settings: Settings) -> dict[str, Any]:
        checks = self._readiness_checks(settings)
        optional_checks = {"cosmos_memory"} if settings.cosmos_configured else set()
        if getattr(settings, "directive_data_configured", False):
            optional_checks.update(
                {
                    "directive_catalog",
                    "directive_artifacts",
                    "directive_search",
                    "directive_mandates",
                }
            )
        if settings.directive_agent_enabled:
            optional_checks.add("directive_hosted_maf")
            optional_checks.add("directive_tool_gateway")
            if not settings.foundry_hosted_enabled:
                optional_checks.add("hosted_tool_gateway")
        completed = await asyncio.gather(
            *(
                run_readiness_check(
                    name,
                    check,
                    settings.readiness_timeout_seconds,
                )
                for name, check in checks.items()
            )
        )
        results = dict(completed)
        for name, result in results.items():
            result["required"] = name not in optional_checks
        ready = all(
            result["status"] == "ok"
            for name, result in results.items()
            if name not in optional_checks
        )
        degraded = sorted(
            name
            for name in optional_checks
            if results.get(name, {}).get("status") != "ok"
        )
        return {
            "status": "ready" if ready else "not_ready",
            "dependencies": results,
            "degraded_dependencies": degraded,
            "agents": {
                agent_type.value: self.agent_available(agent_type, settings)
                for agent_type in visible_agent_types(settings)
            },
        }

    def agent_available(
        self,
        agent_type: AgentType,
        settings: Settings,
    ) -> bool:
        if agent_type not in self.runtime_registry:
            return False
        if settings.resolve_llm_mode() == "mock":
            return True
        if agent_type is AgentType.DIRECTIVE_RAG:
            return bool(
                self.directive_tool_executor.enabled
                and getattr(
                    settings,
                    "directive_hosted_agent_principal_ids",
                    (),
                )
            )
        if agent_type is AgentType.AGENT_FRAMEWORK:
            return bool(
                getattr(settings, "support_hosted_agent_principal_ids", ())
            )
        return True

    async def _initialize_runtime(
        self,
        agent_type: AgentType,
        runtime: AgentRuntime,
    ) -> None:
        name = f"runtime_{agent_type.value}"
        if await self._initialize_component(name, runtime):
            self.runtime_registry[agent_type] = runtime
            return
        await self._close_component(name, runtime)
        self._schedule_runtime_retry(agent_type, runtime)

    def _schedule_runtime_retry(
        self,
        agent_type: AgentType,
        runtime: AgentRuntime,
    ) -> None:
        existing = self._runtime_retry_tasks.get(agent_type)
        if self._closing or existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._retry_runtime(agent_type, runtime),
            name=f"retry-runtime-{agent_type.value}",
        )
        self._runtime_retry_tasks[agent_type] = task
        task.add_done_callback(
            lambda completed, current_type=agent_type: self._retry_finished(
                current_type,
                completed,
            )
        )

    async def _retry_runtime(
        self,
        agent_type: AgentType,
        runtime: AgentRuntime,
    ) -> None:
        delay = _RUNTIME_RETRY_INITIAL_SECONDS
        name = f"runtime_{agent_type.value}"
        while not self._closing and agent_type not in self.runtime_registry:
            await asyncio.sleep(delay)
            if self._closing:
                return
            if await self._initialize_component(name, runtime):
                self.runtime_registry[agent_type] = runtime
                logger.info("[startup] runtime recovered: %s", agent_type.value)
                return
            await self._close_component(name, runtime)
            delay = min(delay * 2, _RUNTIME_RETRY_MAX_SECONDS)

    def _retry_finished(
        self,
        agent_type: AgentType,
        task: asyncio.Task[None],
    ) -> None:
        if self._runtime_retry_tasks.get(agent_type) is task:
            self._runtime_retry_tasks.pop(agent_type, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "[startup] runtime retry stopped: %s",
                agent_type.value,
                exc_info=error,
            )

    @staticmethod
    async def _initialize_component(
        name: str,
        component: ManagedComponent,
    ) -> bool:
        try:
            with span("component.initialize", {"component.name": name}):
                await component.initialize()
            return bool(getattr(component, "enabled", True))
        except Exception:
            logger.exception("[startup] component init failed: %s", name)
            return False

    @staticmethod
    async def _close_component(
        name: str,
        component: ManagedComponent,
    ) -> None:
        try:
            await component.close()
        except Exception:
            logger.exception("[shutdown] component close failed: %s", name)

    def _readiness_checks(
        self,
        settings: Settings,
    ) -> dict[str, Callable[[], Awaitable[None]]]:
        checks: dict[str, Callable[[], Awaitable[None]]] = {}
        if settings.cosmos_configured:
            checks["cosmos_history"] = self.history_store.health_check
            checks["cosmos_profile"] = self.profile_store.health_check
            checks["cosmos_memory"] = self.memory_store.health_check
        if settings.search_configured:
            checks["foundry_iq"] = self.foundry_iq_health.health_check
        if getattr(settings, "directive_data_configured", False):
            checks["directive_catalog"] = self.directive_catalog.health_check
            checks["directive_artifacts"] = self.directive_artifacts.health_check
            checks["directive_search"] = self.directive_search.health_check
            checks["directive_mandates"] = self.directive_mandates.health_check
        if settings.foundry_prompt_enabled:
            runtime = self.runtime_registry.get(AgentType.FOUNDRY_PROMPT)
            checks["foundry_prompt"] = (
                runtime.health_check
                if runtime is not None
                else _unavailable_health_check
            )
        if settings.foundry_hosted_enabled:
            runtime = self.runtime_registry.get(AgentType.AGENT_FRAMEWORK)
            checks["foundry_hosted_maf"] = (
                runtime.health_check
                if runtime is not None
                else _unavailable_health_check
            )
        if settings.directive_agent_enabled:
            runtime = self.runtime_registry.get(AgentType.DIRECTIVE_RAG)
            checks["directive_hosted_maf"] = (
                runtime.health_check
                if runtime is not None
                else _unavailable_health_check
            )
            checks["directive_tool_gateway"] = lambda: _gateway_health(
                settings,
                AgentType.DIRECTIVE_RAG,
            )
        if settings.foundry_hosted_enabled or settings.directive_agent_enabled:
            checks["hosted_tool_gateway"] = lambda: _gateway_health(
                settings,
                AgentType.AGENT_FRAMEWORK,
            )
        return checks


async def _gateway_health(
    settings: Settings,
    agent_type: AgentType,
) -> None:
    principals = (
        getattr(settings, "directive_hosted_agent_principal_ids", ())
        if agent_type is AgentType.DIRECTIVE_RAG
        else getattr(settings, "support_hosted_agent_principal_ids", ())
    )
    if not (settings.agent_gateway_audience and principals):
        raise RuntimeError(
            "Hosted Agent gateway authorization is not configured"
        )


async def _unavailable_health_check() -> None:
    raise RuntimeError("Runtime is unavailable")
