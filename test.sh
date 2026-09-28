#!/bin/zsh
# Run handbook generator test suite locally or inside Docker

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Parse arguments
RUN_LOCAL=false
for arg in "$@"; do
    if [ "$arg" = "--local" ]; then
        RUN_LOCAL=true
    fi
done

if [ "$RUN_LOCAL" = true ]; then
    echo "Running tests locally..."
    if [ ! -d ".venv" ]; then
        echo "Creating virtual environment..."
        python3 -m venv .venv
        source .venv/bin/activate
        pip install -r requirements.txt
    else
        source .venv/bin/activate
    fi
    PYTHONPATH=. pytest -v
else
    echo "Running tests in containerized environment (Docker Compose)..."
    # Ensure Docker daemon is running, otherwise suggest local run
    if ! docker info >/dev/null 2>&1; then
        echo "Error: Docker daemon is not running. Please start Docker, or run tests locally using:"
        echo "  ./test.sh --local"
        exit 1
    fi
    docker-compose down
    docker-compose build
    docker-compose run --rm test-suite
fi
