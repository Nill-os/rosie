import hashlib
import hmac
from pathlib import Path


def value(secrets_dir) -> str:
    key = (Path(secrets_dir) / "session_key").read_text().strip().encode()
    if len(key) < 32:  # board #771 safety-short-key-refused
        raise OSError("secrets/session_key is missing or too short")
    return hmac.new(key, b"assistant-ok", hashlib.sha256).hexdigest()
