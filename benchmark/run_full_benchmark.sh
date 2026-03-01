#!/bin/bash
# Run canonical full benchmark (80 samples, 8 datasets, 40/40/20)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$APP_DIR"

# Load unified environment config (if present)
[[ -f ./.chat.conf ]] && . ./.chat.conf

# Colored output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Full Benchmark Runner (Canonical 80)${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Checks
echo -e "${YELLOW}Running pre-flight checks...${NC}"

# 1. Check Ollama
if ! curl -s http://127.0.0.1:11434/api/tags > /dev/null; then
    echo -e "${RED}❌ Ollama is not running${NC}"
    echo "Start it with: ollama serve"
    exit 1
fi
echo -e "${GREEN}✅ Ollama is running${NC}"

# 2. Check Python environment
if [ ! -f "./chat_env/bin/python" ]; then
    echo -e "${RED}❌ Python environment not found${NC}"
    echo "Create it with: python -m venv chat_env"
    exit 1
fi
echo -e "${GREEN}✅ Python environment ready${NC}"

# 3. Check config file
if [ ! -f "./benchmark/config_full.yaml" ]; then
    echo -e "${RED}❌ config_full.yaml not found${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Config file found${NC}"

# 4. Check available models
echo ""
echo -e "${YELLOW}Verifying models are pulled...${NC}"
MODELS=(
    "gemma3:4b"
    "qwen3:4b"
    "qwen3:8b"
    "deepseek-r1:8b"
    "gemma3:12b"
    "deepseek-r1:14b"
    "qwen3:14b"
    "gpt-oss:20b"
    "magistral:24b"
)

MISSING_MODELS=()
for model in "${MODELS[@]}"; do
    if ! ollama list | grep -q "^$model"; then
        MISSING_MODELS+=("$model")
        echo -e "${YELLOW}⚠️  Missing: $model${NC}"
    else
        echo -e "${GREEN}✅ $model${NC}"
    fi
done

if [ ${#MISSING_MODELS[@]} -gt 0 ]; then
    echo ""
    echo -e "${YELLOW}Warning: ${#MISSING_MODELS[@]} models are missing${NC}"
    echo "Pull them with:"
    for model in "${MISSING_MODELS[@]}"; do
        echo "  ollama pull $model"
    done
    echo ""
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 5. Check disk space
echo ""
echo -e "${YELLOW}Checking disk space...${NC}"
AVAILABLE_GB=$(df -h . | awk 'NR==2 {print $4}' | sed 's/Gi//')
if [ "$AVAILABLE_GB" -lt 10 ]; then
    echo -e "${RED}❌ Low disk space: ${AVAILABLE_GB}GB available${NC}"
    echo "Recommend at least 10GB free"
    exit 1
fi
echo -e "${GREEN}✅ Disk space: ${AVAILABLE_GB}GB available${NC}"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}All checks passed!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Display configuration
echo -e "${YELLOW}Configuration:${NC}"
echo "  Config: benchmark/config_full.yaml"
echo "  Scope: canonical full benchmark"
echo "  Models: 9"
echo "  Datasets: 8"
echo "  Samples: 80"
echo "  Difficulty: 40% easy, 40% medium, 20% hard"
echo "  Total prompt evaluations: 720 (80 x 9)"
echo ""

# Warn about OLLAMA_KEEP_ALIVE
if [ "$OLLAMA_KEEP_ALIVE" != "-1" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}Setting OLLAMA_KEEP_ALIVE=-1${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo "Keeps models loaded during thermal waits."
    echo ""
    export OLLAMA_KEEP_ALIVE=-1
    echo -e "${GREEN}✅ OLLAMA_KEEP_ALIVE set to -1${NC}"
else
    echo -e "${GREEN}✅ OLLAMA_KEEP_ALIVE already set to -1${NC}"
fi
echo ""

# Ask for confirmation
echo -e "${YELLOW}Ready to start benchmark${NC}"
echo ""
read -p "Start now? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Create log directory if needed
mkdir -p log

# Run benchmark
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Starting benchmark...${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Log file: log/benchmark_full_$(date +%Y%m%d_%H%M%S).log"
echo "Live dashboard: http://127.0.0.1:4200/static/docs/benchmark_monitor.html"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop (benchmark will resume from last checkpoint)${NC}"
echo ""

# Run with logging
LOG_FILE="log/benchmark_full_$(date +%Y%m%d_%H%M%S).log"
./chat_env/bin/python benchmark/run_benchmark.py \
    --config benchmark/config_full.yaml \
    2>&1 | tee "$LOG_FILE"

# Check exit status
EXIT_CODE=${PIPEFAIL[0]}
if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✅ Benchmark completed successfully!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "View results at:"
    echo "  http://127.0.0.1:4200/static/docs/benchmark_guided.html"
    echo ""
    echo "Log saved to: $LOG_FILE"
    echo ""

    # Reset OLLAMA_KEEP_ALIVE
    echo -e "${YELLOW}Resetting OLLAMA_KEEP_ALIVE...${NC}"
    unset OLLAMA_KEEP_ALIVE
    echo -e "${GREEN}✅ Done${NC}"
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}❌ Benchmark failed (exit code: $EXIT_CODE)${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    echo "Check log file for errors: $LOG_FILE"
    echo ""
    echo "To resume from last checkpoint:"
    echo "  export OLLAMA_KEEP_ALIVE=-1"
    echo "  ./chat_env/bin/python benchmark/run_benchmark.py --config benchmark/config_full.yaml"
    exit $EXIT_CODE
fi
