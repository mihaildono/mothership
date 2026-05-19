"""
model_detector.py — detect the best Ollama model for this machine's hardware.

Uses `whichllm --json` to rank local LLMs by VRAM fit and benchmark score,
then maps the top HuggingFace model ID to the closest Ollama model name.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ── HuggingFace model ID → Ollama name mapping ────────────────────────────────
# whichllm returns HuggingFace IDs (e.g. "Qwen/Qwen3.6-27B").
# Ollama uses different names. This table maps known model families.
# Format: (regex pattern on lowercased model ID) → ollama base name
# The parameter count (e.g. "27b") is extracted separately and appended.
_FAMILY_MAP: list[tuple[str, str]] = [
    (r"qwen3\.6", "qwen3.6"),
    (r"qwen3", "qwen3"),
    (r"qwen2\.5", "qwen2.5"),
    (r"qwen2", "qwen2"),
    (r"qwen", "qwen"),
    (r"llama-?4", "llama4"),
    (r"llama-?3\.3", "llama3.3"),
    (r"llama-?3\.2", "llama3.2"),
    (r"llama-?3\.1", "llama3.1"),
    (r"llama-?3", "llama3"),
    (r"llama-?2", "llama2"),
    (r"mistral-nemo", "mistral-nemo"),
    (r"mistral", "mistral"),
    (r"mixtral", "mixtral"),
    (r"phi-?4", "phi4"),
    (r"phi-?3\.5", "phi3.5"),
    (r"phi-?3", "phi3"),
    (r"gemma-?3", "gemma3"),
    (r"gemma-?2", "gemma2"),
    (r"gemma", "gemma"),
    (r"deepseek-r2", "deepseek-r2"),
    (r"deepseek-r1", "deepseek-r1"),
    (r"deepseek-v3", "deepseek-v3"),
    (r"deepseek-v2", "deepseek-v2"),
    (r"deepseek", "deepseek"),
    (r"command-?r", "command-r"),
    (r"solar", "solar"),
    (r"yi", "yi"),
    (r"vicuna", "vicuna"),
    (r"orca", "orca"),
    (r"falcon", "falcon"),
    (r"starcoder2", "starcoder2"),
    (r"starcoder", "starcoder"),
    (r"codellama", "codellama"),
    (r"code-?gemma", "codegemma"),
]


class DetectedModel(NamedTuple):
    ollama_name: str  # e.g. "qwen3:27b" — ready for ollama pull/run
    hf_model_id: str  # original HuggingFace model ID
    score: float  # whichllm benchmark score (0-100)
    vram_gb: float  # estimated VRAM required


def _parse_param_size(model_id: str) -> str | None:
    """Extract parameter count tag from a model ID, e.g. '27B' → '27b'."""
    m = re.search(r"(\d+\.?\d*)\s*[xX]?\s*(\d+)[bB]", model_id)
    if m:
        # MoE format e.g. "8x7B" → "8x7b"
        return f"{m.group(1)}x{m.group(2)}b"
    m = re.search(r"(\d+\.?\d*)[bB]", model_id)
    if m:
        return f"{m.group(1)}b"
    return None


def _hf_to_ollama(model_id: str) -> str | None:
    """
    Convert a HuggingFace model ID to an Ollama model name.
    Returns None if no mapping found.
    """
    lower = model_id.lower()
    # Strip org prefix (e.g. "Qwen/Qwen3.6-27B" → "qwen3.6-27b")
    if "/" in lower:
        lower = lower.split("/", 1)[1]

    # Strip common suffixes that don't affect the Ollama name
    for suffix in (
        "-instruct",
        "-chat",
        "-hf",
        "-gguf",
        "-awq",
        "-gptq",
        "-bf16",
        "-fp16",
        "-it",
        "-base",
    ):
        lower = lower.replace(suffix, "")

    param_tag = _parse_param_size(lower)

    for pattern, ollama_base in _FAMILY_MAP:
        if re.search(pattern, lower):
            if param_tag:
                return f"{ollama_base}:{param_tag}"
            return ollama_base

    return None


def _is_available_in_ollama(ollama_name: str, ollama_bin: str) -> bool:
    """Check if a model is already pulled in Ollama."""
    try:
        result = subprocess.run(
            [ollama_bin, "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        base = ollama_name.split(":")[0].lower()
        return any(base in line.lower() for line in result.stdout.splitlines())
    except Exception:
        return False


def detect(ollama_bin: str = "ollama", top_n: int = 5) -> DetectedModel | None:
    """
    Run whichllm, pick the top-ranked model this machine can run,
    and return its Ollama name.

    Returns None if whichllm is not available or detection fails.
    """
    try:
        import shutil

        whichllm = shutil.which("whichllm")
        if not whichllm:
            logger.debug("whichllm not found — skipping auto-detection")
            return None

        logger.info("Detecting best model for this hardware via whichllm...")
        result = subprocess.run(
            [whichllm, "--json", "--top", str(top_n)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "whichllm exited %d: %s", result.returncode, result.stderr[:200]
            )
            return None

        data = json.loads(result.stdout)
        models = data.get("models", [])
        if not models:
            logger.warning("whichllm returned no models")
            return None

        # Walk the ranked list, pick the first one we can map to an Ollama name
        for m in models:
            hf_id = m.get("model_id", "")
            score = float(m.get("score", 0))
            vram = float(m.get("vram_required_gb", 0))
            ollama_name = _hf_to_ollama(hf_id)
            if ollama_name:
                logger.info(
                    "Best model detected: %s (score %.1f, ~%.1f GB VRAM) → ollama: %s",
                    hf_id,
                    score,
                    vram,
                    ollama_name,
                )
                return DetectedModel(
                    ollama_name=ollama_name,
                    hf_model_id=hf_id,
                    score=score,
                    vram_gb=vram,
                )

        logger.warning("whichllm ran but no models mapped to a known Ollama name")
        return None

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse whichllm output: %s", e)
        return None
    except Exception as e:
        logger.warning("Model detection failed: %s", e)
        return None
