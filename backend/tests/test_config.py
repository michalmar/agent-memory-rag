from __future__ import annotations

import dataclasses
import os
import unittest
from unittest.mock import patch

from agent_memory_backend.config import get_settings


class SettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_cache_clear_reloads_environment(self) -> None:
        with patch.dict(os.environ, {"SEARCH_KB": "first"}, clear=False):
            get_settings.cache_clear()
            first = get_settings()

        with patch.dict(os.environ, {"SEARCH_KB": "second"}, clear=False):
            get_settings.cache_clear()
            second = get_settings()

        self.assertEqual(first.search_kb, "first")
        self.assertEqual(second.search_kb, "second")
        self.assertIsNot(first, second)

    def test_settings_are_immutable_and_normalized(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": " PRODUCTION ",
                "FOUNDRY_PROMPT_ENABLED": "yes",
                "HOSTED_AGENT_PRINCIPAL_IDS": "one, two three",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.app_environment, "production")
        self.assertTrue(settings.foundry_prompt_enabled)
        self.assertEqual(
            settings.hosted_agent_principal_ids,
            ("one", "two", "three"),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            settings.auth_mode = "entra"  # type: ignore[misc]

    def test_derived_configuration_matches_runtime_inputs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_MODE": "",
                "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
                "COSMOS_ENDPOINT": "https://example.documents.azure.com/",
                "SEARCH_ENDPOINT": "https://example.search.windows.net",
                "ENTRA_TENANT_ID": "tenant",
                "ENTRA_AUDIENCE": "audience",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            settings = get_settings()

        self.assertEqual(settings.resolve_llm_mode(), "real")
        self.assertTrue(settings.cosmos_configured)
        self.assertTrue(settings.search_configured)
        self.assertTrue(settings.entra_configured)
