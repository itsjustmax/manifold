#!/bin/bash
# Manifold bootstrap — one paste, one manifold.
#
#   curl -sL https://<any-manifold>/setup.sh | bash
#
# Served by every live manifold with __SOURCE_URL__ templated to its own
# address, so the newborn manifold downloads its code from its parent and
# announces itself back to it: the mesh grows by one paste.
#
# Env knobs: MANIFOLD_DIR (checkout dir, default ./manifold),
# MANIFOLD_SETUP_ONLY=1 (prepare everything, don't start serving).

main() {
set -e
export PIP_DISABLE_PIP_VERSION_CHECK=1
SOURCE="__SOURCE_URL__"
case "$SOURCE" in __*) SOURCE="";; esac      # raw file, not served copy
REPO="https://github.com/itsjustmax/manifold"
DIR="${MANIFOLD_DIR:-manifold}"

say() { printf '\n\033[1mmanifold · %s\033[0m\n' "$*"; }

# -- python 3.10+ ------------------------------------------------------
if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install Python 3.10+ (https://python.org) and rerun."
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "python3 is $(python3 -V 2>&1) — Manifold needs 3.10+."
  echo "macOS: brew install python3 · Debian/Ubuntu: apt install python3.11"
  exit 1
fi

# -- the code: parent manifold first, canonical repo second ---------------
if [ -e "$DIR/manifold/app.py" ]; then
  say "using existing $DIR/"
elif [ -n "$SOURCE" ] && curl -fsL -H "ngrok-skip-browser-warning: 1" \
      -o /tmp/manifold-src.tgz "$SOURCE/source.tar.gz" 2>/dev/null; then
  tar xzf /tmp/manifold-src.tgz && rm -f /tmp/manifold-src.tgz
  [ "$DIR" != manifold ] && mv manifold "$DIR"
  say "downloaded source from your parent manifold: $SOURCE"
elif command -v git >/dev/null; then
  git clone --depth 1 "$REPO" "$DIR"
  say "cloned $REPO"
else
  echo "couldn't reach a manifold and git is missing — install git or retry."
  exit 1
fi
cd "$DIR"

# -- environment --------------------------------------------------------
say "setting up python environment (venv + fastapi/uvicorn)"
python3 -m venv .venv
.venv/bin/pip -q install -r requirements.txt
say "environment ready"

# -- ngrok --------------------------------------------------------------
NEXT="cd $DIR && .venv/bin/python -m manifold.serve"
[ -n "$SOURCE" ] && NEXT="$NEXT --announce $SOURCE"
if ! command -v ngrok >/dev/null; then
  say "one thing left: ngrok (the tunnel that puts you on the internet)"
  echo "  1. install:   https://ngrok.com/download  (macOS: brew install ngrok)"
  echo "  2. authtoken: ngrok config add-authtoken <token>  (free account)"
  echo "  3. launch:    $NEXT"
  echo
  echo "or play locally right now: cd $DIR && .venv/bin/python -m manifold.serve --no-tunnel"
  exit 0
fi

if [ "${MANIFOLD_SETUP_ONLY:-}" = 1 ]; then
  say "setup complete — launch with: $NEXT"
  exit 0
fi

# -- go -----------------------------------------------------------------
say "starting your manifold (Ctrl-C stops it)"
if [ -n "$SOURCE" ]; then
  exec .venv/bin/python -m manifold.serve --announce "$SOURCE"
else
  exec .venv/bin/python -m manifold.serve
fi
}
main "$@"
