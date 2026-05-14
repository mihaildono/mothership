#!/usr/bin/env bash
# nebula/manage-child.sh — Add, revoke, or regenerate child tokens.
#
# Usage:
#   ./manage-child.sh revoke  <child_id>          # disable a child immediately
#   ./manage-child.sh add     <child_id>          # add a new child (generates cert + token)
#   ./manage-child.sh retoken <child_id>          # rotate a child's auth token
#   ./manage-child.sh list                        # show all registered children

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOTHER_ENV="$SCRIPT_DIR/../mother/.env"
CERTS_DIR="$SCRIPT_DIR/certs"
BUNDLES_DIR="$SCRIPT_DIR/bundles"
BIN_DIR="$SCRIPT_DIR/bin"

CMD="${1:-list}"
CHILD_ID="${2:-}"

_require_env() {
    if [[ ! -f "$MOTHER_ENV" ]]; then
        echo "ERROR: mother/.env not found. Run setup-mother.sh first." >&2
        exit 1
    fi
}

_update_tokens_line() {
    # Rebuild MOTHER_CHILD_TOKENS from all # child: lines
    ALL_TOKENS="$(grep "^# child:.*:token=" "$MOTHER_ENV" | sed 's/^# child:\(.*\):token=\(.*\)/\1=\2/' | paste -sd ',' -)"
    if grep -q "^MOTHER_CHILD_TOKENS=" "$MOTHER_ENV"; then
        python3 -c "
import re
content = open('$MOTHER_ENV').read()
content = re.sub(r'^MOTHER_CHILD_TOKENS=.*$', 'MOTHER_CHILD_TOKENS=${ALL_TOKENS}', content, flags=re.MULTILINE)
open('$MOTHER_ENV', 'w').write(content)
"
    else
        echo "MOTHER_CHILD_TOKENS=${ALL_TOKENS}" >> "$MOTHER_ENV"
    fi
}

case "$CMD" in

list)
    _require_env
    echo "Registered children:"
    echo ""
    if grep -q "^# child:" "$MOTHER_ENV"; then
        grep "^# child:.*:token=" "$MOTHER_ENV" | while IFS= read -r line; do
            child_id="$(echo "$line" | sed 's/^# child:\(.*\):token=.*/\1/')"
            printf "  %-20s  (revoke: ./manage-child.sh revoke %s)\n" "$child_id" "$child_id"
        done
    else
        echo "  (none)"
    fi
    echo ""
    ;;

revoke)
    _require_env
    if [[ -z "$CHILD_ID" ]]; then echo "Usage: $0 revoke <child_id>" >&2; exit 1; fi

    if ! grep -q "^# child:${CHILD_ID}:" "$MOTHER_ENV"; then
        echo "ERROR: child '${CHILD_ID}' not found in mother/.env" >&2
        exit 1
    fi

    # Remove the child's comment+token lines from .env
    python3 -c "
lines = open('$MOTHER_ENV').readlines()
lines = [l for l in lines if not (l.startswith('# child:${CHILD_ID}:') or False)]
open('$MOTHER_ENV', 'w').writelines(lines)
"
    _update_tokens_line

    # Remove bundle + token file
    rm -f "$BUNDLES_DIR/${CHILD_ID}.tar.gz" "$BUNDLES_DIR/${CHILD_ID}.token"

    echo "==> Child '${CHILD_ID}' revoked."
    echo "    Restart the mother to apply: cd ../mother && ./start.sh"
    ;;

retoken)
    _require_env
    if [[ -z "$CHILD_ID" ]]; then echo "Usage: $0 retoken <child_id>" >&2; exit 1; fi

    if ! grep -q "^# child:${CHILD_ID}:" "$MOTHER_ENV"; then
        echo "ERROR: child '${CHILD_ID}' not found in mother/.env" >&2
        exit 1
    fi

    NEW_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

    # Replace the token line
    python3 -c "
import re
content = open('$MOTHER_ENV').read()
content = re.sub(
    r'^(# child:${CHILD_ID}:token=).*$',
    r'\g<1>${NEW_TOKEN}',
    content, flags=re.MULTILINE
)
open('$MOTHER_ENV', 'w').write(content)
"
    _update_tokens_line

    # Regenerate download token
    python3 - "$BUNDLES_DIR/${CHILD_ID}.token" << 'PYEOF'
import sys, json, secrets, time
data = {"token": secrets.token_hex(24), "expires_at": time.time() + 600}
with open(sys.argv[1], "w") as f:
    json.dump(data, f)
PYEOF
    chmod 600 "$BUNDLES_DIR/${CHILD_ID}.token"

    DL_TOKEN="$(python3 -c "import json; print(json.load(open('$BUNDLES_DIR/${CHILD_ID}.token'))['token'])")"
    PUBLIC_IP="$(curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null)"

    echo "==> Token rotated for '${CHILD_ID}'."
    echo "    Restart the mother to apply: cd ../mother && ./start.sh"
    echo ""
    echo "    New one-time install command for the child:"
    echo "    curl -fsSL \"http://${PUBLIC_IP}:8765/bundle/${CHILD_ID}?token=${DL_TOKEN}\" -o ${CHILD_ID}.tar.gz && tar -xzf ${CHILD_ID}.tar.gz && cd ${CHILD_ID} && ./install.sh"
    echo ""
    echo "    (Link expires in 10 minutes)"
    ;;

add)
    if [[ -z "$CHILD_ID" ]]; then echo "Usage: $0 add <child_id>" >&2; exit 1; fi
    # Delegate to setup-mother.sh with just this new child
    exec "$SCRIPT_DIR/setup-mother.sh" "" --children "$CHILD_ID"
    ;;

*)
    echo "Usage: $0 {list|add|revoke|retoken} [child_id]" >&2
    exit 1
    ;;
esac
