export OLLAMA_KEEP_ALIVE=-1
cd /Users/sevi/local-chat
./scripts/run.sh stop
rm -f ./log/*
./scripts/run.sh start
python ./benchmark/run_benchmark.py --config ./benchmark/config_light.yaml --light &
