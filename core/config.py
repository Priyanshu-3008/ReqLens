"""Provider settings. Every provider speaks the OpenAI-compatible Chat Completions API,
so switching provider is just a different base URL, key and model name."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str | None       # env var holding the API key (None = no key needed)
    default_model: str
    note: str


PROVIDERS: dict[str, Provider] = {
    "Groq": Provider(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        default_model="openai/gpt-oss-120b",
        note="Free key at console.groq.com. Groq retires model names over time — "
             "if you get a 404, check console.groq.com/docs/models for the current one.",
    ),
    "Gemini": Provider(
        name="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        key_env="GEMINI_API_KEY",
        default_model="gemini-2.5-flash",
        note="Free key at aistudio.google.com. Free-tier prompts may be used for training.",
    ),
    "Ollama (local)": Provider(
        name="Ollama (local)",
        base_url="http://localhost:11434/v1",
        key_env=None,
        default_model="llama3.1",
        note="Runs on your machine, no key. Install Ollama and `ollama pull llama3.1`.",
    ),
}

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "Groq")

# Tuning knobs
TEMPERATURE = 0.1
MAX_OUTPUT_TOKENS = 4096
EXTRACT_CHUNK_CHARS = 12_000   # document is split on paragraph boundaries above this size
SCORE_BATCH_SIZE = 12          # requirements per testability-scoring call
CONTEXT_LIST_MAX_CHARS = 10_000  # include the full requirement list for consistency checks below this


def resolve_key(provider: Provider, override: str | None = None) -> str:
    """Key priority: value typed in the sidebar > Streamlit secrets > environment."""
    if override:
        return override.strip()
    if provider.key_env is None:
        return "ollama"  # any non-empty string works for local Ollama
    try:
        import streamlit as st  # optional: only present when running the app
        if provider.key_env in st.secrets:
            return str(st.secrets[provider.key_env])
    except Exception:
        pass
    return os.getenv(provider.key_env, "")
