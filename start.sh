#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if ! python3 - <<'PY'
from sni_finder.settings import load_settings
import sys

settings = load_settings()
sys.exit(0 if str(getattr(settings, "vless_source", "")).strip() else 1)
PY
then
  echo "vless_source is not configured in config/scanner_settings.json."
  echo "Running interactive setup..."
  if ! python3 scanner.py configure; then
    echo
    echo "Configuration failed or was cancelled."
    echo "Log file: logs/scanner.log"
    read -r -p "Press Enter to close..."
    exit 1
  fi

  if ! python3 - <<'PY'
from sni_finder.settings import load_settings
import sys

settings = load_settings()
sys.exit(0 if str(getattr(settings, "vless_source", "")).strip() else 1)
PY
  then
    echo
    echo "vless_source is still empty. Please set it and relaunch."
    echo "Log file: logs/scanner.log"
    read -r -p "Press Enter to close..."
    exit 1
  fi
fi

python3 scanner.py
EXIT_CODE=$?
echo
if [[ ${EXIT_CODE} -ne 0 ]]; then
  echo "Scanner exited with an error. Code=${EXIT_CODE}"
else
  echo "Scanner closed."
fi
echo "Log file: logs/scanner.log"
read -r -p "Press Enter to close..."
exit ${EXIT_CODE}
