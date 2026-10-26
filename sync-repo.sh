#!/bin/bash
# board #829 why-syncs-live-slot-only
cd "$(dirname "$0")" || exit 1
ACTIVE=$(tr -d ' \n' < "$HOME/rosie-ops/active" 2>/dev/null)
if [ "$ACTIVE" = "B" ] && [ -d "$HOME/assistant-b/.git" ]; then cd "$HOME/assistant-b" || exit 1; fi
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "${1:-Auto-sync $(date '+%Y-%m-%d %H:%M')}" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
fi
for r in $(git remote); do
  # board #829 why-rollback-own-branch
  git push -q "$r" HEAD:main 2>/dev/null || git push -q "$r" "HEAD:refs/heads/rolled-back-$(date +%m%d-%H%M)" || exit 1
done
echo "synced ($ACTIVE): $(git log --oneline | head -1)"
