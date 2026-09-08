#!/usr/bin/env bash
# One-time setup for the blackjack development environment (macOS / Linux).
# Mirrors environment/bootstrap.ps1. Safe to re-run.
#
# Usage:  ./environment/bootstrap.sh [--with-rust] [--with-app]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_RUST=false
WITH_APP=false
for arg in "$@"; do
  case "$arg" in
    --with-rust) WITH_RUST=true ;;
    --with-app)  WITH_APP=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }
ok()   { printf '    %s\n' "$1"; }

echo "Blackjack solver -- environment bootstrap"
echo "Repository: $REPO"

step "Checking for uv"
if command -v uv >/dev/null 2>&1; then
  ok "uv already installed: $(uv --version)"
else
  ok "Installing uv (MIT/Apache-2.0)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

step "Syncing the Python environment"
cd "$REPO"
uv python install          # honours .python-version (3.13)
uv sync --all-extras
ok "Environment ready. uv.lock is the source of truth."

if $WITH_RUST; then
  step "Checking for the Rust toolchain"
  if command -v cargo >/dev/null 2>&1; then
    ok "cargo already installed: $(cargo --version)"
  else
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    export PATH="$HOME/.cargo/bin:$PATH"
  fi
  step "Building the native core"
  uv run maturin develop --release --manifest-path crates/blackjack-core/Cargo.toml \
    || echo "    Native core build failed; the engine works without it, only slower."
else
  step "Rust toolchain"
  ok "Skipped. Re-run with --with-rust for the native accelerator."
fi

if $WITH_APP; then
  step "Installing web dependencies"
  command -v npm >/dev/null 2>&1 || { echo "npm not found; install Node.js 20+"; exit 1; }
  (cd "$REPO/apps/web" && npm install)
fi

step "Verifying"
uv run bj solve --rules vegas6-h17
ok "The solver runs."

cat <<'MSG'

Done.

Next steps:
    uv run bj chart --rules vegas6-h17 --importance
    uv run bj explain T6 T
    uv run pytest -m "not slow"

Documentation: markdown/ReadMe.md
MSG
