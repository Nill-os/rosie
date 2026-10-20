#!/bin/bash
# Replace the access code and sign out EVERY device (PC browser, phone app). You will enter the new code once on each.
# Usage: ./rotate-code.sh        (prints the new code in your terminal)
cd "$(dirname "$0")" || exit 1
python3 - <<'PY'
import secrets, string, pathlib
alpha = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
code = "-".join("".join(secrets.choice(alpha) for _ in range(4)) for _ in range(3))
d = pathlib.Path("secrets")
(d / "access_code").write_text(code + "\n")
(d / "session_key").write_text(secrets.token_hex(32) + "\n")
for f in ("access_code", "session_key"):
    (d / f).chmod(0o600)
print("New access code:", code)
PY
echo "All devices are signed out. Open Rosie on each and enter the new code."
