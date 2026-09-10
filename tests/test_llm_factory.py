"""Unit tests for Universal Multi-Provider LLM Factory."""

import unittest
import os
from unittest.mock import patch, MagicMock
from src.llm_factory import (
    detect_llm_provider,
    resolve_model_for_provider,
    call_llm,
    call_vertex_gemini_rest,
    call_gemini_api_rest,
    call_openai_compatible_rest,
    call_anthropic_rest,
)
from src.config import DEFAULT_CONFIG

class TestLLMFactory(unittest.TestCase):

    def test_provider_detection(self):
        # Explicit env override
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}):
            self.assertEqual(detect_llm_provider(), "openai")

        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini_api"}):
            self.assertEqual(detect_llm_provider(), "gemini_api")

        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}):
            self.assertEqual(detect_llm_provider(), "anthropic")

        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}):
            self.assertEqual(detect_llm_provider(), "ollama")

        # Key-based detection
        with patch.dict(os.environ, {"LLM_PROVIDER": "auto", "GEMINI_API_KEY": "AIzaSyFakeKey"}, clear=True):
            self.assertEqual(detect_llm_provider(), "gemini_api")

        with patch.dict(os.environ, {"LLM_PROVIDER": "auto", "OPENAI_API_KEY": "sk-fakekey"}, clear=True):
            self.assertEqual(detect_llm_provider(), "openai")

        with patch.dict(os.environ, {"LLM_PROVIDER": "auto", "ANTHROPIC_API_KEY": "sk-ant-fake"}, clear=True):
            self.assertEqual(detect_llm_provider(), "anthropic")

    def test_model_resolution(self):
        # OpenAI resolution
        self.assertEqual(resolve_model_for_provider("gemini-2.5-flash", "openai", "worker"), "gpt-4o-mini")
        self.assertEqual(resolve_model_for_provider("gemini-2.5-pro", "openai", "executive"), "gpt-4o")
        self.assertEqual(resolve_model_for_provider("gpt-4o", "openai"), "gpt-4o")

        # Anthropic resolution
        self.assertEqual(resolve_model_for_provider("gemini-2.5-flash", "anthropic", "worker"), "claude-3-5-haiku-20241022")
        self.assertEqual(resolve_model_for_provider("gemini-2.5-pro", "anthropic", "executive"), "claude-3-5-sonnet-20241022")

        # Vertex / Gemini API resolution
        self.assertEqual(resolve_model_for_provider("gemini-2.5-flash", "gemini_api", "worker"), "gemini-2.5-flash")
        self.assertEqual(resolve_model_for_provider("gemini-2.5-pro", "vertex", "executive"), "gemini-2.5-pro")

    @patch("src.llm_factory.call_openai_compatible_rest")
    def test_dispatch_to_openai(self, mock_openai):
        mock_openai.return_value = "Response from OpenAI"
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}):
            resp = call_llm("Hello test", model_tier="worker")
            self.assertEqual(resp, "Response from OpenAI")
            mock_openai.assert_called_once()
            args, kwargs = mock_openai.call_args
            self.assertEqual(kwargs["model_name"], "gpt-4o-mini")

    @patch("src.llm_factory.call_gemini_api_rest")
    def test_dispatch_to_gemini_api(self, mock_gemini):
        mock_gemini.return_value = "Response from Gemini API"
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini_api"}):
            resp = call_llm("Hello test", model_tier="executive")
            self.assertEqual(resp, "Response from Gemini API")
            mock_gemini.assert_called_once()
            args, kwargs = mock_gemini.call_args
            self.assertEqual(kwargs["model_name"], "gemini-2.5-pro")

    @patch("src.llm_factory.call_anthropic_rest")
    def test_dispatch_to_anthropic(self, mock_anthropic):
        mock_anthropic.return_value = "Response from Anthropic"
        with patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}):
            resp = call_llm("Hello test", model_tier="executive")
            self.assertEqual(resp, "Response from Anthropic")
            mock_anthropic.assert_called_once()
            args, kwargs = mock_anthropic.call_args
            self.assertEqual(kwargs["model_name"], "claude-3-5-sonnet-20241022")

if __name__ == "__main__":
    unittest.main()
