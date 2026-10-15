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
"""
import os


def mp_persist() -> bool:
    return os.environ.get("AG_MP_PERSIST") == "1"


def memory_snapshot() -> bool:
    return os.environ.get("AG_MEMORY_SNAPSHOT") == "1"


def id_variant() -> str:
    return os.environ.get("AG_ID_VARIANT", "")


def as_dict() -> dict:
    return {"mp_persist": mp_persist(), "memory_snapshot": memory_snapshot(),
            "id_variant": id_variant()}
