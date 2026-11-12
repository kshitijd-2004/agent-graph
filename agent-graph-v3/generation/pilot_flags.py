"""Switches for the MP / ID delivery pilot. All off by default, so the original
v3 benchmark reproduces unchanged.

AG_MP_PERSIST=1       attack side: after memory poisoning fires on a role, that
                      role's later writes to the same key keep the poisoned value.
AG_MEMORY_SNAPSHOT=1  workflow side (clean and attack alike): a stage that receives
                      a handoff also gets the current shared memory records, and in
                      coordinator_workers the coordinator must write memory before
                      dispatching. Needs its own matched clean runs.
AG_ID_VARIANT=key_findings
                      attack side: input disregard names the sender's actual
                      findings and tells the receiver not to rely on them. Source
                      access is unchanged.
AG_BENIGN_VARIANTS=memory_enabled
                      planning only: emit clean runs just for these execution
                      variants (comma separated), e.g. the snapshot arm only
                      needs new memory_enabled clean runs.
"""
import os


def mp_persist() -> bool:
    return os.environ.get("AG_MP_PERSIST") == "1"


def memory_snapshot() -> bool:
    return os.environ.get("AG_MEMORY_SNAPSHOT") == "1"


def id_variant() -> str:
    return os.environ.get("AG_ID_VARIANT", "")


def benign_variants() -> list:
    v = os.environ.get("AG_BENIGN_VARIANTS", "")
    return [x.strip() for x in v.split(",") if x.strip()]


def as_dict() -> dict:
    return {"mp_persist": mp_persist(), "memory_snapshot": memory_snapshot(),
            "id_variant": id_variant()}
