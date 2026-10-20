#!/bin/bash
# Commit any changes and push them to every git remote you added (your own private repo). Always syncs the LIVE code slot (A or B).
# Usage: ./sync-repo.sh ["commit message"]     (the daily timer runs it with no message)
cd "$(dirname "$0")" || exit 1
ACTIVE=$(tr -d ' \n' < "$HOME/rosie-ops/active" 2>/dev/null)
if [ "$ACTIVE" = "B" ] && [ -d "$HOME/assistant-b/.git" ]; then cd "$HOME/assistant-b" || exit 1; fi
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "${1:-Auto-sync $(date '+%Y-%m-%d %H:%M')}" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
fi
for r in $(git remote); do
  # main follows what is live; after a rollback the live code is OLDER than main, so it goes to its own branch instead of rewriting history
  git push -q "$r" HEAD:main 2>/dev/null || git push -q "$r" "HEAD:refs/heads/rolled-back-$(date +%m%d-%H%M)" || exit 1
done
echo "synced ($ACTIVE): $(git log --oneline | head -1)"
