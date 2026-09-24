# SciFact materializer

Builds the 100 `research_synthesis` fixtures (`workspace_fixtures/res_scifact_*`) from SciFact (Wadden et al., EMNLP 2020).

Data: download https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz and unpack it into `data/` next to this script (so `data/data/corpus.jsonl` exists).

    python3 gen_research.py --n 100 --out ../../workspace_fixtures

Each fixture has two claims and three real abstracts: one paper supports one claim, another contradicts the other claim (SciFact's human labels), and Paper C is an unrelated distractor. 300 papers and 200 claims, no reuse. Each claim is also listed under `seeds` for the semantic (NLI) measure.
