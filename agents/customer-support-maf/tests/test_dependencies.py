import asyncio
import unittest
from importlib.metadata import version
from pathlib import Path


REQUIREMENTS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "customer-support-maf"
    / "requirements.txt"
)
KNOWN_GOOD_SUPPORT_STACK = {
    "agent-framework-core": "1.11.0",
    "agent-framework-foundry": "1.10.1",
    "agent-framework-foundry-hosting": "1.0.0a260709",
    "agent-framework-openai": "1.10.1",
    "openai": "2.46.0",
}


class DependencyPinTests(unittest.TestCase):
    def test_known_good_support_stack_is_fully_pinned(self) -> None:
        requirements = dict(
            line.strip().split("==", maxsplit=1)
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
            and not line.lstrip().startswith("#")
            and "==" in line
        )

        actual = {
            package: requirements.get(package)
            for package in KNOWN_GOOD_SUPPORT_STACK
        }
        self.assertEqual(actual, KNOWN_GOOD_SUPPORT_STACK)

    def test_installed_stack_matches_support_pins(self) -> None:
        for package, expected in KNOWN_GOOD_SUPPORT_STACK.items():
            with self.subTest(package=package):
                self.assertEqual(version(package), expected)

    def test_stateless_support_request_omits_encrypted_reasoning(self) -> None:
        from agent_framework import Message
        from agent_framework_openai._chat_client import RawOpenAIChatClient

        client = object.__new__(RawOpenAIChatClient)
        options = asyncio.run(
            client._prepare_options(
                [Message("user", ["Hello"])],
                {"model": "gpt-4o-mini", "store": False},
            )
        )

        self.assertNotIn(
            "reasoning.encrypted_content",
            options.get("include", []),
        )


if __name__ == "__main__":
    unittest.main()
