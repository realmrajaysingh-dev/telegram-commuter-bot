import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from studio.factory01_gemini_adapter import Factory01GeminiAdapter, GeminiAdapterError


class FakeModels:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []
    def generate_content(self, *, model, contents):
        self.calls.append((model, contents))
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return SimpleNamespace(text=value)


class FakeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('SECRET MUST NOT LEAK')


class Factory01GeminiAdapterTests(unittest.TestCase):
    def test_disabled_never_calls_provider(self):
        models = FakeModels(['ready'])
        adapter = Factory01GeminiAdapter(client=SimpleNamespace(models=models))
        with self.assertRaises(GeminiAdapterError):
            adapter.generate('test')
        self.assertEqual(models.calls, [])

    def test_success_is_real_but_private_unverified(self):
        models = FakeModels(['  Actual model output  '])
        result = Factory01GeminiAdapter(client=SimpleNamespace(models=models)).generate('Hello', enabled=True)
        self.assertEqual(result['text'], 'Actual model output')
        self.assertTrue(result['execution_performed'])
        self.assertFalse(result['research_verified'])
        self.assertFalse(result['publication_enabled'])
        self.assertEqual(len(models.calls), 1)

    def test_503_retries_once_and_recovers(self):
        models = FakeModels([FakeError(503), 'Okay'])
        waits = []
        result = Factory01GeminiAdapter(client=SimpleNamespace(models=models), sleep=waits.append).generate('Hello', enabled=True)
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(waits, [1.0])

    def test_503_exhaustion_safe_error(self):
        models = FakeModels([FakeError(503), FakeError(503)])
        adapter = Factory01GeminiAdapter(client=SimpleNamespace(models=models), sleep=lambda _: None)
        with self.assertRaisesRegex(GeminiAdapterError, '503') as caught:
            adapter.generate('Hello', enabled=True)
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(len(models.calls), 2)

    def test_429_no_retry_and_no_secret_leak(self):
        models = FakeModels([FakeError(429)])
        with self.assertRaisesRegex(GeminiAdapterError, '429') as caught:
            Factory01GeminiAdapter(client=SimpleNamespace(models=models)).generate('Hello', enabled=True)
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(len(models.calls), 1)

    def test_invalid_model_prompt_and_attempts(self):
        with self.assertRaises(ValueError):
            Factory01GeminiAdapter(model='unknown')
        with self.assertRaises(ValueError):
            Factory01GeminiAdapter(max_attempts=4)
        a = Factory01GeminiAdapter(client=SimpleNamespace(models=FakeModels([])))
        with self.assertRaises(ValueError):
            a.generate('', enabled=True)

    def test_missing_key_fails_without_provider(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}):
            with self.assertRaisesRegex(GeminiAdapterError, 'not configured'):
                Factory01GeminiAdapter().generate('Hello', enabled=True)
