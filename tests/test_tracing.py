import os
import unittest
from unittest.mock import patch

from app.ai import tracing


def _patch_settings(**overrides):
    values = {
        "langsmith_tracing": True,
        "langsmith_api_key": "test-key",
        "langsmith_project": "proj",
        "langsmith_endpoint": None,
        "langsmith_hide_inputs": False,
        "langsmith_hide_outputs": True,
    }
    values.update(overrides)
    return patch.multiple(tracing.settings, **values)


class ConfigureTracingTests(unittest.TestCase):
    def test_disabled_by_default_leaves_environment_alone(self) -> None:
        with _patch_settings(langsmith_tracing=False), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(tracing.configure_tracing())
            self.assertNotIn("LANGSMITH_TRACING", os.environ)

    def test_enabled_without_api_key_stays_off(self) -> None:
        with _patch_settings(langsmith_api_key=None), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(tracing.configure_tracing())
            self.assertNotIn("LANGSMITH_TRACING", os.environ)

    def test_enabled_exports_settings_to_environment(self) -> None:
        with _patch_settings(), patch.dict(os.environ, {}, clear=True):
            self.assertTrue(tracing.configure_tracing())
            self.assertEqual(os.environ["LANGSMITH_TRACING"], "true")
            self.assertEqual(os.environ["LANGSMITH_API_KEY"], "test-key")
            self.assertEqual(os.environ["LANGSMITH_PROJECT"], "proj")
            self.assertEqual(os.environ["LANGSMITH_HIDE_INPUTS"], "false")
            self.assertEqual(os.environ["LANGSMITH_HIDE_OUTPUTS"], "true")
            self.assertNotIn("LANGSMITH_ENDPOINT", os.environ)

    def test_endpoint_is_exported_when_set(self) -> None:
        with (
            _patch_settings(langsmith_endpoint="https://eu.api.smith.langchain.com"),
            patch.dict(os.environ, {}, clear=True),
        ):
            tracing.configure_tracing()
            self.assertEqual(
                os.environ["LANGSMITH_ENDPOINT"], "https://eu.api.smith.langchain.com"
            )
