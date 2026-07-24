import asyncio
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


REQUIREMENTS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "directive-rag-maf"
    / "requirements.txt"
)
HOSTING_REQUIREMENT = "agent-framework-foundry-hosting==1.0.0b260722"


class DependencyCompatibilityTests(unittest.TestCase):
    def test_hosting_dependency_pins_reasoning_replay_fix(self) -> None:
        requirements = {
            line.strip()
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn(HOSTING_REQUIREMENT, requirements)

    def test_stateless_directive_request_includes_encrypted_reasoning(self) -> None:
        from agent_framework import Message
        from agent_framework_openai._chat_client import RawOpenAIChatClient

        client = object.__new__(RawOpenAIChatClient)
        options = asyncio.run(
            client._prepare_options(
                [Message("user", ["Hello"])],
                {"model": "gpt-5.6-sol", "store": False},
            )
        )

        self.assertIn(
            "reasoning.encrypted_content",
            options.get("include", []),
        )

    def test_hosting_round_trips_encrypted_reasoning_content(self) -> None:
        probe = textwrap.dedent(
            """
            from agent_framework import Content
            from agent_framework_foundry_hosting._responses import (
                _emit_reasoning_output,
                _reasoning_item_to_contents,
            )
            from azure.ai.agentserver.responses import ResponseEventStream

            stream = ResponseEventStream(
                response_id="response-test",
                model="model-test",
            )
            stream.emit_created()
            stream.emit_in_progress()
            content = Content.from_text_reasoning(
                text="summary",
                protected_data="opaque-replay-payload",
            )
            emitted = list(_emit_reasoning_output(stream, [content]))[-1]
            assert emitted.item.encrypted_content == "opaque-replay-payload"

            replayed = _reasoning_item_to_contents(emitted.item)
            assert replayed[0].protected_data == "opaque-replay-payload"
            """
        )

        completed = subprocess.run(
            [sys.executable, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
