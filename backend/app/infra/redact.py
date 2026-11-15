import re

_SECRET_RE = re.compile(
    r"(?i)((?:aws_)?(?:secret|password|passwd|access[_-]?key|credential|authorization|token)[_a-z0-9-]*)=([^\s&,;]+)"
)
_AKIA_RE = re.compile(r"AKIA[0-9A-Z]{8,}")
_BEARER_RE = re.compile(r"(?i)(Bearer\s+)\S+")
_AUTH_RE = re.compile(r"(?i)(Authorization:\s*)\S+")


def redact(text: str) -> str:
    if not text:
        return text
    hidden = _SECRET_RE.sub(r"\1=<redacted>", text)
    hidden = _BEARER_RE.sub(r"\1<redacted>", hidden)
    hidden = _AUTH_RE.sub(r"\1<redacted>", hidden)
    return _AKIA_RE.sub("<redacted>", hidden)
