# MP / ID arms, sourced by pilot.sbatch.
# Pilot arms (p*): 20 tasks per family (every 15th, sorted), done Oct 2026.
# Full arms (f*): all tasks, only the two versions that worked in the pilot:
#   MP + memory shown on handoff (own memory_enabled clean runs, 3 reps) and
#   ID quoting the sender's real findings (compared with the v3 clean runs).
# Arms that reuse the original v3 clean runs have BENIGN=0.
FAMS="pmf pmr psf psr pif pir"
FULL="fsf fsr fsc fif fir fic"
NJOBS=1
FIXSTEP=15
SNAP="AG_MP_PERSIST=1 AG_MEMORY_SNAPSHOT=1 AG_BENIGN_VARIANTS=memory_enabled"
FINGLOB="workspace_fixtures/fin_finqa_*"
RESGLOB="workspace_fixtures/res_scifact_*"
CRGLOB="workspace_fixtures_v3/code_review_swe_*"
case "$1" in
  pmf) FAMILY=financial_analysis; FIXGLOB=$FINGLOB; BENIGN=0; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="AG_MP_PERSIST=1" ;;
  pmr) FAMILY=research_synthesis; FIXGLOB=$RESGLOB; BENIGN=0; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="AG_MP_PERSIST=1" ;;
  psf) FAMILY=financial_analysis; FIXGLOB=$FINGLOB; BENIGN=3; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="$SNAP" ;;
  psr) FAMILY=research_synthesis; FIXGLOB=$RESGLOB; BENIGN=3; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="$SNAP" ;;
  pif) FAMILY=financial_analysis; FIXGLOB=$FINGLOB; BENIGN=0; LEPS=LEP_INPUT_DISREGARD; PILOT_ENV="AG_ID_VARIANT=key_findings" ;;
  pir) FAMILY=research_synthesis; FIXGLOB=$RESGLOB; BENIGN=0; LEPS=LEP_INPUT_DISREGARD; PILOT_ENV="AG_ID_VARIANT=key_findings" ;;
  # full runs: 4,200 runs per MP family (1,500 attack + 2,700 clean), 1,500 per ID family
  fsf) FAMILY=financial_analysis; FIXGLOB=$FINGLOB; FIXSTEP=1; NJOBS=3; BENIGN=3; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="$SNAP" ;;
  fsr) FAMILY=research_synthesis; FIXGLOB=$RESGLOB; FIXSTEP=1; NJOBS=3; BENIGN=3; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="$SNAP" ;;
  fsc) FAMILY=code_review;        FIXGLOB=$CRGLOB;  FIXSTEP=1; NJOBS=3; BENIGN=3; LEPS=LEP_MEMORY_POISONING; PILOT_ENV="$SNAP" ;;
  fif) FAMILY=financial_analysis; FIXGLOB=$FINGLOB; FIXSTEP=1; NJOBS=1; BENIGN=0; LEPS=LEP_INPUT_DISREGARD; PILOT_ENV="AG_ID_VARIANT=key_findings" ;;
  fir) FAMILY=research_synthesis; FIXGLOB=$RESGLOB; FIXSTEP=1; NJOBS=1; BENIGN=0; LEPS=LEP_INPUT_DISREGARD; PILOT_ENV="AG_ID_VARIANT=key_findings" ;;
  fic) FAMILY=code_review;        FIXGLOB=$CRGLOB;  FIXSTEP=1; NJOBS=1; BENIGN=0; LEPS=LEP_INPUT_DISREGARD; PILOT_ENV="AG_ID_VARIANT=key_findings" ;;
  "") ;;
  *) return 1 ;;
esac
