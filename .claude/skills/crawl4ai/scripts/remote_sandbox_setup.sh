#!/usr/bin/env bash
# One-shot setup for Claude Code remote sandbox (ephemeral container).
# Installs crawl4ai into a dedicated venv and bridges the pre-installed
# Playwright Chromium (/opt/pw-browsers) to whatever browser revision the
# venv's Playwright expects, so `playwright install` is never needed.
# Re-run after every container restart. Idempotent.
set -euo pipefail

VENV=/root/.venv-crawl4ai
PWDIR=/opt/pw-browsers

[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet crawl4ai

have_chromium=$(ls -d "$PWDIR"/chromium-[0-9]* 2>/dev/null | grep -v headless | head -1 || true)
have_shell=$(ls -d "$PWDIR"/chromium_headless_shell-[0-9]* 2>/dev/null | head -1 || true)
dry=$("$VENV/bin/playwright" install --dry-run chromium 2>/dev/null || true)
want_chromium=$(echo "$dry" | grep -oE 'chromium-[0-9]+' | grep -v headless | head -1 || true)
want_shell=$(echo "$dry" | grep -oE 'chromium_headless_shell-[0-9]+' | head -1 || true)

if [ -n "$have_chromium" ] && [ -n "$want_chromium" ] && [ ! -e "$PWDIR/$want_chromium" ]; then
  ln -s "$have_chromium" "$PWDIR/$want_chromium"
fi
if [ -n "$have_shell" ] && [ -n "$want_shell" ] && [ ! -e "$PWDIR/$want_shell" ]; then
  ln -s "$have_shell" "$PWDIR/$want_shell"
fi
# Newer Playwright expects chrome-headless-shell-linux64/chrome-headless-shell;
# older bundles ship chrome-linux/headless_shell. Bridge the layout.
if [ -n "$have_shell" ] && [ -f "$have_shell/chrome-linux/headless_shell" ]; then
  mkdir -p "$have_shell/chrome-headless-shell-linux64"
  ln -sf ../chrome-linux/headless_shell "$have_shell/chrome-headless-shell-linux64/chrome-headless-shell"
fi

ln -sf "$VENV/bin/crwl" /usr/local/bin/crwl 2>/dev/null || true

echo "crawl4ai $("$VENV/bin/python" -c 'from crawl4ai.__version__ import __version__; print(__version__)') ready."
echo "Python: $VENV/bin/python   CLI: crwl"
echo "Remember: pass the egress proxy to the browser, e.g."
echo '  BrowserConfig(headless=True, proxy=os.environ.get("HTTPS_PROXY"))'
