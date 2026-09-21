from __future__ import annotations

import re
from typing import Any, Dict, List, Set

REDACTED_PLACEHOLDER = "[REDACTED]"

# Denied keys (case-insensitive)
SENSITIVE_KEY_PATTERNS = re.compile(
    r"(?:api_?key|secret|token|auth(?:orization)?|password|credential|private_key)",
    re.IGNORECASE,
)

# Common secret token formats (e.g. Bearer tokens, OpenAI keys, AWS keys)
SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{36}", re.IGNORECASE),
]


def redact_secrets(obj: Any) -> Any:
    """Recursively scrub sensitive keys and token patterns from payloads."""
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if SENSITIVE_KEY_PATTERNS.search(str(k)):
                cleaned[k] = REDACTED_PLACEHOLDER
            else:
                cleaned[k] = redact_secrets(v)
        return cleaned
    elif isinstance(obj, list):
        return [redact_secrets(item) for item in obj]
    elif isinstance(obj, str):
        val = obj
        for pattern in SENSITIVE_VALUE_PATTERNS:
            val = pattern.sub(REDACTED_PLACEHOLDER, val)
        return val
    else:
        return obj
