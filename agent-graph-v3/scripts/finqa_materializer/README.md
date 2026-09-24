# FinQA materializer

Builds the 100 `financial_analysis` fixtures (`workspace_fixtures/fin_finqa_*`) from FinQA (Chen et al., EMNLP 2021).

Data: download `train.json`, `dev.json`, `test.json` from https://github.com/czyssrs/FinQA (dataset/) into `data/` next to this script as `finqa_train.json`, `finqa_dev.json`, `finqa_test.json`.

    python3 gen_financial.py --n 100 --out ../../workspace_fixtures

Each fixture is one 10-K page: `documents/report.md` (page text and table, authoritative), `documents/call_transcript.md` (restates two key figures), `notes/draft_figures.md` (the trap: figures shifted 6 to 15%). One fixture per company. Static checks run at build time and are written to each manifest's `validation` block.
