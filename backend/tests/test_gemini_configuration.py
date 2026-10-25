import os
import unittest
from unittest.mock import patch

import call_summary
import main


class GeminiKeyConfigurationTests(unittest.TestCase):
    def test_process_environment_key_takes_priority_and_is_trimmed(self) -> None:
        for module in (main, call_summary):
            with self.subTest(module=module.__name__):
                with patch.dict(os.environ, {"GEMINI_API_KEY": "  env-test-key  "}):
                    with patch.object(
                        module,
                        "dotenv_values",
                        return_value={"GEMINI_API_KEY": "file-test-key"},
                    ):
                        self.assertEqual(
                            module._get_gemini_api_key(),
                            "env-test-key",
                        )

    def test_blank_environment_key_falls_back_to_trimmed_dotenv_key(self) -> None:
        for module in (main, call_summary):
            with self.subTest(module=module.__name__):
                with patch.dict(os.environ, {"GEMINI_API_KEY": "  "}):
                    with patch.object(
                        module,
                        "dotenv_values",
                        return_value={"GEMINI_API_KEY": "  file-test-key  "},
                    ):
                        self.assertEqual(
                            module._get_gemini_api_key(),
                            "file-test-key",
                        )

    def test_missing_keys_return_none(self) -> None:
        for module in (main, call_summary):
            with self.subTest(module=module.__name__):
                with patch.dict(os.environ, {}, clear=True):
                    with patch.object(module, "dotenv_values", return_value={}):
                        self.assertIsNone(module._get_gemini_api_key())

    def test_blank_values_in_both_sources_return_none(self) -> None:
        for module in (main, call_summary):
            with self.subTest(module=module.__name__):
                with patch.dict(os.environ, {"GEMINI_API_KEY": "  "}):
                    with patch.object(
                        module,
                        "dotenv_values",
                        return_value={"GEMINI_API_KEY": "  "},
                    ):
                        self.assertIsNone(module._get_gemini_api_key())


if __name__ == "__main__":
    unittest.main()
