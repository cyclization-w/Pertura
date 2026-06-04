"""LLM API helpers — key resolution, model selection, JSON extraction/repair."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def _config() -> dict:
    cfg = Path.home() / ".pertura" / "config.json"
    legacy = Path.home() / ".blackboard" / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text())
    return json.loads(legacy.read_text()) if legacy.exists() else {}


def _api_key() -> str | None:
    return os.getenv("OPENAI_API_KEY") or _config().get("openai_api_key")


def _anthropic_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or _config().get("anthropic_api_key")


def _model(provider: str = "openai") -> str:
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    return os.getenv("OPENAI_MODEL") or "gpt-4o"


def _call_llm(system: str, user: str, output_schema: dict,
              *, provider: str = "openai") -> dict:
    from pertura.core.fixtures import RecordedLLMFixtures, fixture_mode, llm_fixture_hash
    request_hash = llm_fixture_hash("structured_llm", {
        "provider": provider,
        "model": _model(provider),
        "system": system,
        "user": user,
        "schema": output_schema,
    })
    fixtures = RecordedLLMFixtures()
    if fixture_mode() in {"replay", "strict"}:
        item = fixtures.require(request_hash)
        return item.get("response", {})
    if provider == "anthropic":
        result = _call_anthropic(system, user, output_schema)
    else:
        result = _call_openai(system, user, output_schema)
    fixtures.put(request_hash, result, metadata={"provider": provider, "model": _model(provider)})
    return result


def _call_openai(system: str, user: str, output_schema: dict) -> dict:
    from openai import OpenAI
    key = _api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set.")
    client = OpenAI(api_key=key, base_url=os.getenv("OPENAI_BASE_URL") or None)
    response = client.chat.completions.create(
        model=_model("openai"),
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.1,
        max_tokens=2048,
        response_format={"type": "json_schema",
                        "json_schema": {"name": "output", "strict": True,
                                       "schema": output_schema}},
    )
    content = response.choices[0].message.content or "{}"
    return _repair_json(content)


def _call_anthropic(system: str, user: str, output_schema: dict) -> dict:
    from anthropic import Anthropic
    key = _anthropic_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set.")
    client = Anthropic(api_key=key,
                      base_url=os.getenv("ANTHROPIC_BASE_URL") or None)
    response = client.messages.create(
        model=_model("anthropic"), system=system,
        messages=[{"role": "user", "content": user}],
        temperature=0.1, max_tokens=2048,
    )
    text = response.content[0].text if response.content else "{}"
    return _extract_json(text)


def _repair_json(text: str) -> dict:
    """Repair common JSON malformations (unbalanced quotes, truncation)."""
    text = text.strip()
    # Count and balance braces
    open_b = text.count("{") - text.count("}")
    text += "}" * max(0, open_b)
    # Count and balance brackets
    open_a = text.count("[") - text.count("]")
    text += "]" * max(0, open_a)
    # Add missing closing quote if inside a string
    in_string = False
    for c in text:
        if c == '"':
            in_string = not in_string
    if in_string:
        text += '"'
    return json.loads(text)


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from text."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return _repair_json(text[start:end + 1])
    return {}
