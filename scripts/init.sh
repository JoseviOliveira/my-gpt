export OLLAMA_KEEP_ALIVE=-1
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
cd "$APP_DIR"
./scripts/run.sh stop
rm -f ./log/*
./scripts/run.sh start
python ./benchmark/run_benchmark.py --config ./benchmark/config_light.yaml --light &
