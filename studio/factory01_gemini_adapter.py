"""VOOCKS Factory 01: explicit, opt-in Gemini text execution boundary.

No import of bot.py, source browsing, research verification, DB writes or publication.
Calling generate() is a REAL provider request, not a simulated worker run.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Any


class GeminiAdapterError(RuntimeError):
    """Safe public failure; avoid exposing provider exceptions or credentials."""


class Factory01GeminiAdapter:
    def __init__(self, *, model: str = 'gemini-3.1-flash-lite',
                 client: Any = None, max_attempts: int = 2,
                 sleep: Callable[[float], None] = time.sleep):
        if model not in {'gemini-3.1-flash-lite', 'gemini-3.5-flash-lite',
                         'gemini-3.8-flash'}:
            raise ValueError('Model not on the Factory 01 allowlist')
        if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
            raise ValueError('At most two explicit attempts allowed')
        self.model = model
        self.max_attempts = max_attempts
        self._client = client
        self._sleep = sleep

    def generate(self, prompt: str, *, enabled: bool = False) -> dict:
        """Generate private text only when user explicitly opts in.

        SDK may itself retry transient failures internally. This method adds at
        most one extra outer attempt, and never retries non-503 failures.
        """
        if enabled is not True:
            raise GeminiAdapterError('Real AI execution requires explicit opt-in')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 6000:
            raise ValueError('Prompt must contain 1-6000 characters')
        if self._client is None:
            if not os.getenv('GEMINI_API_KEY'):
                raise GeminiAdapterError('GEMINI_API_KEY is not configured')
            try:
                from google import genai
            except ImportError as exc:
                raise GeminiAdapterError('Install google-genai in Codespaces') from exc
            # Only created here, never while importing this module.
            self._client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=prompt)
                result = getattr(response, 'text', None)
                if not isinstance(result, str) or not result.strip():
                    raise GeminiAdapterError('Provider returned no usable text')
                return {'status': 'generated_private_unverified', 'model': self.model,
                        'text': result.strip(), 'attempts': attempt,
                        'execution_performed': True, 'research_verified': False,
                        'editorially_approved': False, 'publication_enabled': False}
            except GeminiAdapterError:
                raise
            except Exception as exc:
                # Use a structured status code where present; no raw exception
                # strings, prompts, keys or provider payloads in failure output.
                code = getattr(exc, 'code', None)
                if code is None:
                    code = getattr(exc, 'status_code', None)
                if code == 503 and attempt < self.max_attempts:
                    self._sleep(1.0)
                    continue
                if code == 503:
                    raise GeminiAdapterError('Gemini temporarily unavailable (503)') from None
                if code == 429:
                    raise GeminiAdapterError('Gemini quota or rate limit reached (429)') from None
                raise GeminiAdapterError('Gemini request failed; check provider status') from None
        raise GeminiAdapterError('Gemini request did not complete')
