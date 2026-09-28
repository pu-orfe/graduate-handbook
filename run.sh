#!/bin/zsh
# Princeton Graduate Handbook Generator CLI Wrapper
#
#   ./run.sh [cli flags]            run natively (PDF via Microsoft Word on macOS, else LibreOffice)
#   ./run.sh --docker [cli flags]   run in the same container CI uses (LibreOffice rendering)

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Bot-protection header for the scrape; kept out of git (see README).
if [ -f .env ]; then
    set -a
    source ./.env
    set +a
fi

if [ "$1" = "--docker" ]; then
    shift
    docker-compose build generate
    docker-compose run --rm generate "$@"
    exit $?
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

PYTHONPATH=. python3 -m handbook_generator.cli "$@"
