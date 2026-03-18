#!/bin/bash
# Open Voice SDK Interruption Test Runner
# Usage: ./run_tests.sh [runtime_url]

set -e

RUNTIME_URL="${1:-ws://localhost:8011}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Open Voice SDK Interruption Test Runner"
echo "=========================================="
echo "Runtime URL: $RUNTIME_URL"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 not found"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."
python3 -c "import websockets, numpy" 2>/dev/null || {
    echo "Installing dependencies..."
    pip install websockets numpy
}

echo "✓ Dependencies OK"
echo ""

# Run tests
echo "Starting tests..."
echo ""
cd "$SCRIPT_DIR"
python3 test_realtime.py "$RUNTIME_URL"

echo ""
echo "=========================================="
echo "Tests complete!"
echo "=========================================="
