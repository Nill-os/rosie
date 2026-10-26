#!/bin/bash
set -e
[ "$EUID" -eq 0 ] || { echo "Run with sudo: sudo $0"; exit 1; }
U="${SUDO_USER:-$USER}"
[[ "$U" =~ ^[a-z_][a-z0-9_-]*$ ]] && [ "$U" != root ] || { echo "Run it with sudo from your own account, not as root."; exit 1; }
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-rosie.rules <<RULE
// Rosie: $U may start, stop and restart Rosie's own service without a password.
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "assistant.service" &&
        ["start", "stop", "restart"].indexOf(action.lookup("verb")) >= 0 &&
        subject.user == "$U") {
        return polkit.Result.YES;
    }
});
RULE
chmod 644 /etc/polkit-1/rules.d/50-rosie.rules
echo "Done: $U can start and stop Rosie without a password."
