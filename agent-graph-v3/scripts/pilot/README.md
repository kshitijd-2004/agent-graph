# MP / ID delivery pilot

Switches (all off by default, see `generation/pilot_flags.py`):

- `AG_MP_PERSIST=1`: after memory poisoning fires on a role, later writes by that role to the same key keep the poison (not a new origin).
- `AG_MEMORY_SNAPSHOT=1`: a stage that receives a handoff also gets the shared memory records; the coordinator writes memory before dispatching. Workflow change, so these arms get their own clean runs.
- `AG_ID_VARIANT=key_findings`: input disregard quotes the sender's findings and says not to rely on them; source access unchanged.
- `AG_BENIGN_VARIANTS=memory_enabled`: plan clean runs only for the listed execution variants.

Job scripts (Delta): `pilot.sbatch`, `pilot_families.sh` (arms), `pilot_submit.sh` (20-task pilot), `full_submit.sh` (all tasks, the two arms that worked).

Analysis (read saved traces, no GPU):

- `matched_effect.py`: attack vs matched clean (same task, topology, variant), fixture-bootstrap 95% CI.
- `mp_id_check.py`: MP / ID delivered, read by another agent, read by the final agent, used downstream, action change vs clean noise.
- `prop_check.py`: content-based propagation (injected details in another agent's input, in the final answer).
- `mp_split.py`: MP effect split by adoption, re-reading sources, poisoned key.
- `full_table.py`: every recorded metric by attack and topology.
- `res_attack.py`: research verdict parsing for the flip metric.
