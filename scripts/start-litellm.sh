#!/bin/bash
# Start LiteLLM proxy for forgerace
# Required before running ./fr run with goose/aider agents
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/../litellm_config.yaml"
LITELLM_BIN="$HOME/.local/share/pipx/venvs/litellm/bin/litellm"

if [ ! -f "$LITELLM_BIN" ]; then
    echo "LiteLLM not installed. Run: pipx install 'litellm[proxy]'"
    exit 1
fi

echo "Starting LiteLLM proxy on :4000..."
echo "Models: llama-70b, devstral-123b, qwen-122b, gpt-oss-120b"
echo "Key: fr-local-dev"
echo ""
exec "$LITELLM_BIN" --config "$CONFIG" --port 4000 --host 127.0.0.1
