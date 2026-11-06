#!/bin/bash
set -e
cd "$(dirname "$0")"
. ./netretry.sh
PY=.venv/bin/python
OUT=$($PY app/backup.py backup)
NAME=$(tr -d ' \n' < secrets/backup_repo 2>/dev/null || true)
if [ -z "$NAME" ]; then echo "backup kept on this PC: $(basename "$OUT")"; exit 0; fi
REPO="$HOME/backups/rosie-data-repo"
if [ ! -d "$REPO/.git" ]; then
  mkdir -p "$(dirname "$REPO")"
  net_wait || true
  net_retry sh -c 'gh repo clone "$1" "$2" 2>/dev/null || git clone -q "https://github.com/$1.git" "$2"' _ "$NAME" "$REPO"
fi
cp "$OUT" "$REPO/"
cd "$REPO"
ls -t rosie-data-*.gpg 2>/dev/null | tail -n +31 | xargs -r rm -f
git add -A
LOGIN=$(gh api user --jq .login 2>/dev/null || echo rosie)
git -c user.name="$LOGIN" -c user.email="$(gh api user --jq .id 2>/dev/null || echo 0)+$LOGIN@users.noreply.github.com" commit -q -m "Encrypted backup $(basename "$OUT")" || true
net_wait || true
net_retry git push -q origin HEAD && echo "backup pushed: $(basename "$OUT")"
