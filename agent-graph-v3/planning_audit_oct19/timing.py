"""Read-only: walltime and per-scenario runtime evidence from production logs."""
import collections
import glob
import json
import os
import re

# ── Slurm shard walltime ───────────────────────────────────────────────────
durations = []
start_missing = []
for log in sorted(glob.glob('/u/kdhande/agent-graph/prod-3291540_*.log')):
    text = open(log).read()
    m = re.search(r'Started:\s+(.+)', text)
    if not m:
        start_missing.append(log)
        continue
    start = m.group(1).strip()
    # elapsed: parse from the "Benchmark shard X/Y..." timestamp to end-of-log
    m2 = re.search(r'Starting benchmark shard \d+/\d+\.\.\.\n(.+)', text)
    if not m2:
        continue
    bench_start = m2.group(1).strip()
    import datetime
    try:
        t0 = datetime.datetime.strptime(bench_start, '%a %b %d %I:%M:%S %p %Z %Y')
    except ValueError:
        continue
    # last timestamped line in the .err file
    err = log[:-4] + '.err'
    last = None
    if os.path.exists(err):
        for line in open(err):
            mm = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),', line)
            if mm:
                last = mm.group(1)
    if last is None:
        continue
    t1 = datetime.datetime.strptime(last, '%Y-%m-%d %H:%M:%S')
    durations.append(((t1 - t0).total_seconds() / 3600.0, os.path.basename(log)))

durations.sort()
print('shards with measurable walltime: %d' % len(durations))
if durations:
    vals = [d[0] for d in durations]
    n = len(vals)
    print('min  %.2f h  (%s)' % (vals[0], durations[0][1]))
    print('p25  %.2f h' % vals[n // 4])
    print('med  %.2f h' % vals[n // 2])
    print('p75  %.2f h' % vals[3 * n // 4])
    print('p90  %.2f h' % vals[int(n * 0.9)])
    print('max  %.2f h  (%s)' % (vals[-1], durations[-1][1]))
    over = [d for d in durations if d[0] >= 2.0]
    print('shards at/over the 2h limit: %d' % len(over))
    for d in over:
        print('   %.2f h  %s' % d)

# ── Per-scenario runtime from records ──────────────────────────────────────
runtimes = []
by_term = collections.defaultdict(list)
for path in glob.glob('production_run/node_*/benchmark_records.jsonl'):
    for line in open(path):
        r = json.loads(line)
        rt = r.get('runtime_seconds')
        if rt is None:
            continue
        runtimes.append(rt)
        by_term[r.get('termination_reason', '')].append(rt)
runtimes.sort()
n = len(runtimes)
print()
print('per-scenario runtime: n=%d  mean=%.1fs  p50=%.1fs  p90=%.1fs  p99=%.1fs  max=%.1fs'
      % (n, sum(runtimes) / n, runtimes[n // 2], runtimes[int(n * .9)],
         runtimes[int(n * .99)], runtimes[-1]))
print('by termination reason (n, mean s):')
for term, vals in sorted(by_term.items(), key=lambda x: -len(x[1])):
    print('  %-32s n=%-6d mean=%7.1fs p50=%7.1fs' % (term, len(vals),
          sum(vals) / len(vals), sorted(vals)[len(vals) // 2]))
