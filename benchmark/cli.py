import argparse
import logging
import os
import sys
from pathlib import Path

from benchmark.runner import BenchmarkRunner

ROOT_DIR = Path(__file__).parent.parent
LOG_DIR = ROOT_DIR / "log"
LOG_FILE = LOG_DIR / "benchmark.log"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            else:
                if " #" in value or "\t#" in value or value.startswith("#") or value.endswith("#"):
                    value = value.split("#", 1)[0].rstrip()
            os.environ[key] = value
    except Exception:
        return


def _configure_logging() -> None:
    _load_env_file(ROOT_DIR / ".chat_userconf")
    os.environ.setdefault("LOG_LEVEL", "DEBUG")

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode="w")
    file_handler.setFormatter(formatter)

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])
    root_logger.setLevel(log_level)


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Run LLM benchmark suite")
    parser.add_argument("--config", default="benchmark/config_full.yaml", help="Path to config file")
    judge_group = parser.add_mutually_exclusive_group()
    judge_group.add_argument(
        "--judge-model",
        help="Override judge model used for llm_judge grading",
    )
    judge_group.add_argument(
        "--no-judge",
        action="store_true",
        help="Disable llm_judge grading rules during this run",
    )
    parser.add_argument(
        "--stop-on-empty",
        action="store_true",
        help="Stop the benchmark when an Ollama response is empty",
    )
    parser.add_argument(
        "--no-evaluation",
        action="store_true",
        help="Skip all on-the-fly evaluation/grading and store raw outputs/KPIs only",
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Run only a specific task (repeatable). Example: --task mmlu --task chat_json",
    )
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not resume a previous run; always start a fresh run",
    )
    resume_group.add_argument(
        "--resume-run-id",
        help="Resume a specific existing run_id instead of auto-selecting the latest resumable run",
    )
    args = parser.parse_args()

    task_filter = {name.strip() for name in args.task if name.strip()}
    if args.no_resume:
        os.environ["BENCHMARK_NO_RESUME"] = "1"

    runner = BenchmarkRunner(
        args.config,
        stop_on_empty=args.stop_on_empty,
        task_filter=task_filter or None,
        resume_run_id=args.resume_run_id,
        judge_model_override=args.judge_model,
        no_judge=args.no_judge,
        no_evaluation=args.no_evaluation,
    )
    runner.run()


if __name__ == "__main__":
    main()
