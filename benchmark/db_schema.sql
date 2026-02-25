-- Benchmark Database Schema
-- This schema supports both academic benchmarks (Part A) and chat UX evaluation (Part B)

-- Primary record of each benchmark execution
CREATE TABLE IF NOT EXISTS benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT DEFAULT 'running',
    hardware_profile TEXT,
    ollama_version TEXT,
    notes TEXT
);

-- Run scope derived from the benchmark config (models/datasets/expected counts)
CREATE TABLE IF NOT EXISTS benchmark_run_scope (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    config_path TEXT,
    config_name TEXT,
    light_mode INTEGER DEFAULT 0,
    models_json TEXT,
    datasets_json TEXT,
    models_total INTEGER,
    datasets_total INTEGER,
    tasks_total INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Live run counters stored in DB for dashboards
CREATE TABLE IF NOT EXISTS benchmark_run_state (
    run_id TEXT PRIMARY KEY,
    status TEXT,
    updated_at TEXT,
    current_model TEXT,
    current_dataset TEXT,
    current_task TEXT,
    current_task_label TEXT,
    current_sample_id TEXT,
    current_dialog_id TEXT,
    model_index INTEGER,
    dataset_index INTEGER,
    task_index INTEGER,
    tasks_total INTEGER,
    progress_percent REAL,
    models_total INTEGER,
    datasets_total INTEGER,
    models_completed INTEGER,
    light_mode INTEGER,
    recent_metrics_json TEXT,
    recent_gpu_json TEXT,
    recent_gpu_temp_json TEXT,
    recent_cpu_json TEXT,
    recent_disk_io_json TEXT,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Run scope objects
CREATE TABLE IF NOT EXISTS benchmark_run_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_index INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

CREATE TABLE IF NOT EXISTS benchmark_run_datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    dataset_label TEXT,
    dataset_kind TEXT,
    dataset_index INTEGER NOT NULL,
    tasks_total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

CREATE TABLE IF NOT EXISTS benchmark_run_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    task_label TEXT,
    task_index INTEGER NOT NULL,
    task_kind TEXT,
    sample_id TEXT,
    dialog_id TEXT,
    turn_index INTEGER,
    status TEXT DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id),
    UNIQUE(run_id, model_name, dataset_name, task_name, task_label)
);

-- Model metadata for each run
CREATE TABLE IF NOT EXISTS benchmark_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_id TEXT,
    parameters_b REAL,
    quantization TEXT,
    size_gb REAL,
    warmup_ttft_ms INTEGER,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Task-level aggregations (academic tasks)
CREATE TABLE IF NOT EXISTS benchmark_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    category TEXT NOT NULL,
    samples_total INTEGER,
    samples_correct INTEGER,
    accuracy REAL,
    avg_ttft_ms REAL,
    avg_tokens_per_sec REAL,
    avg_total_time_ms REAL,
    started_at TEXT,
    completed_at TEXT,
    disk_io_avg_mbps REAL,
    disk_io_max_mbps REAL,
    disk_metrics_available INTEGER,
    gpu_util_avg REAL,
    gpu_util_max REAL,
    gpu_metrics_available INTEGER,
    gpu_temp_avg REAL,
    gpu_temp_max REAL,
    cpu_util_avg REAL,
    cpu_util_max REAL,
    cpu_metrics_available INTEGER,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Per-sample granularity for debugging and analysis
CREATE TABLE IF NOT EXISTS benchmark_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    request_payload TEXT,
    prompt TEXT,
    prompt_clean TEXT,
    expected TEXT,
    expected_clean TEXT,
    response TEXT,
    response_clean TEXT,
    correct INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    ttft_ms INTEGER,
    tokens_per_sec REAL,
    total_time_ms INTEGER,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Dialog/session-level aggregates for Conversational Chat Track
CREATE TABLE IF NOT EXISTS chat_dialogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dataset TEXT NOT NULL,
    dialog_id TEXT NOT NULL,
    turns_total INTEGER NOT NULL,
    turns_compliant INTEGER NOT NULL,
    session_compliant INTEGER NOT NULL,
    avg_ttft_ms REAL,
    p95_ttft_ms REAL,
    avg_tokens_per_sec REAL,
    jitter_ms REAL,
    late_turn_recall REAL,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    disk_io_avg_mbps REAL,
    gpu_util_avg REAL,
    gpu_temp_avg REAL,
    cpu_util_avg REAL,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Per-turn records with compliance flags and streaming metrics
CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dataset TEXT NOT NULL,
    dialog_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    request_payload TEXT,
    user_text TEXT,
    user_text_clean TEXT,
    response TEXT,
    response_clean TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    ttft_ms INTEGER,
    tokens_per_sec REAL,
    total_time_ms INTEGER,
    jitter_ms REAL,
    compliance_json TEXT,
    violations TEXT,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Per-task judgments from external or internal judges
CREATE TABLE IF NOT EXISTS benchmark_opinions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    task_name TEXT NOT NULL,
    judge TEXT NOT NULL,
    is_aggregate INTEGER DEFAULT 0,
    verdict TEXT NOT NULL,
    quality REAL,
    speed REAL,
    resources REAL,
    flags TEXT,
    recommendations TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES benchmark_runs(run_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_samples_run_model ON benchmark_samples(run_id, model_name);
CREATE INDEX IF NOT EXISTS idx_samples_task ON benchmark_samples(task_name);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON benchmark_tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_chat_dialogs ON chat_dialogs(run_id, model_name, dataset);
CREATE INDEX IF NOT EXISTS idx_chat_turns ON chat_turns(run_id, dialog_id);
CREATE INDEX IF NOT EXISTS idx_run_models ON benchmark_run_models(run_id, model_name);
CREATE INDEX IF NOT EXISTS idx_run_datasets ON benchmark_run_datasets(run_id, model_name, dataset_name);
CREATE INDEX IF NOT EXISTS idx_run_tasks ON benchmark_run_tasks(run_id, model_name, dataset_name);
