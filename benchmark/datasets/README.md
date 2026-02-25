# Benchmark Datasets (Current)

## Canonical full pack

Folder: `benchmark/datasets/canonical_full_80/`

Includes 8 datasets x 10 samples each:

- Shortform (50 total)
  - `logic_deduction_10.jsonl`
  - `critical_thinking_10.jsonl`
  - `physics_10.jsonl`
  - `cs_engineering_10.jsonl`
  - `history_10.jsonl`
- Chat (30 total)
  - `chat_memory_10.jsonl`
  - `chat_safety_10.jsonl`
  - `chat_instruction_10.jsonl`

Difficulty target for this canonical pack:
- 40% easy
- 40% medium
- 20% hard

## File format

JSONL, one record per sample/dialog.
Typical fields:
- `id`
- `difficulty`
- `question` or prompt-like content
- `answer` or `answer_type`
- `grading`
- optional `metadata`

## Grading styles

- exact/contains constraints
- structured rule checks (word/sentence constraints)
- LLM-judge tags for policy tasks (for example some safety prompts)

## Legacy datasets

The folder also contains larger non-canonical datasets (15/20/25/40 sample sets) used in earlier experimentation and subset/light configs. They are kept for compatibility and ad-hoc analysis.
