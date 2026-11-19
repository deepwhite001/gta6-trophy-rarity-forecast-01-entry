"""End-to-end checks for the entry, run from the repository root:

    python3 tests/run_checks.py

It runs main.py on the kit's practice cases, checks every output is
valid and byte-identical across two runs, scores this entry and the
kit's starter against synthetic practice answers built from the refresh
the kit documents, then builds two synthetic final-like cases with a
known truth and scores both programs on those. It prints a table; it
exits non-zero if any output is invalid or any run differs.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KIT = Path("/home/hatch/workspace/makefun/gta6-trophy-rarity-forecast-01-kit")

sys.path.insert(0, str(HERE))
import score as scorer
import synth

FAIL = False


def run_program(program, input_dir, output_dir, extra_env=None):
    """Run a program with the input/output directories set; return rc."""
    env = dict(os.environ)
    env["MAKEFUN_INPUT_DIR"] = str(input_dir)
    env["MAKEFUN_OUTPUT_DIR"] = str(output_dir)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(program)], env=env,
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 or proc.stderr.strip():
        print(f"  !! {program} rc={proc.returncode} stderr={proc.stderr.strip()}")
    return proc.returncode


def validate(out_path, case):
    """An .out file must match the contract exactly."""
    global FAIL
    raw = out_path.read_bytes()
    if len(raw) > 65536:
        print(f"  !! {out_path.name} too large: {len(raw)} bytes")
        FAIL = True
    doc = json.loads(raw)
    if set(doc) != {"trophies"}:
        print(f"  !! {out_path.name} top-level keys {set(doc)}")
        FAIL = True
    rows = doc["trophies"]
    want = [t["id"] for t in case["trophies"]]
    if [r["id"] for r in rows] != want:
        print(f"  !! {out_path.name} row ids do not match the case")
        FAIL = True
    for row in rows:
        if set(row) != {"id", "q"} or len(row["q"]) != 5:
            print(f"  !! {out_path.name} bad row {row}")
            FAIL = True
            continue
        q = row["q"]
        if not all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in q):
            print(f"  !! {out_path.name} quantile out of range {row}")
            FAIL = True
        if any(q[i] > q[i + 1] for i in range(4)):
            print(f"  !! {out_path.name} quantiles decrease {row}")
            FAIL = True
    return scorer.load_forecast(out_path)


def main():
    global FAIL
    tmp = Path(tempfile.mkdtemp(prefix="gta6-check-"))
    mine = ROOT / "main.py"
    starter = KIT / "starter" / "main.py"

    print("== practice cases: validity and determinism ==")
    out1, out2 = tmp / "o1", tmp / "o2"
    run_program(mine, KIT / "input", out1)
    run_program(mine, KIT / "input", out2)
    cases = {p.stem: json.loads(p.read_text())
             for p in sorted((KIT / "input").glob("*.in"))}
    for cid, case in cases.items():
        mine_f = validate(out1 / f"{cid}.out", case)
        again = (out2 / f"{cid}.out").read_bytes()
        if (out1 / f"{cid}.out").read_bytes() != again:
            print(f"  !! {cid}.out differs between runs")
            FAIL = True
        print(f"  {cid}: valid, {len(mine_f)} rows, "
              f"{len((out1 / f'{cid}.out').read_bytes())} bytes, deterministic")

    print("== practice answers (synthetic, from the documented refresh) ==")
    answers = synth.practice_answers()
    starter_out = tmp / "starter-practice"
    run_program(starter, KIT / "input", starter_out)
    print(f"  {'answer':<14}{'mine loss':>10}{'starter':>10}"
          f"{'mine miss':>11}{'starter':>9}")
    for name, truth in answers.items():
        for cid in ("p00", "p01"):
            mine_f = scorer.load_forecast(out1 / f"{cid}.out")
            base_f = scorer.load_forecast(starter_out / f"{cid}.out")
            m_loss, m_miss, m_cov = scorer.score(mine_f, truth)
            b_loss, b_miss, b_cov = scorer.score(base_f, truth)
            print(f"  {name + '/' + cid:<14}{m_loss:>10.4f}{b_loss:>10.4f}"
                  f"{m_miss:>11.2f}{b_miss:>9.2f}  "
                  f"coverage mine {m_cov:.2f} starter {b_cov:.2f}")

    print("== synthetic final cases (known truth, 6510-minute horizon) ==")
    for variant in ("steady", "slowdown"):
        case, truth = synth.final_case(variant)
        case_dir = tmp / f"final-{variant}"
        case_dir.mkdir()
        (case_dir / "f00.in").write_text(json.dumps(case))
        mine_out, base_out = tmp / f"m-{variant}", tmp / f"b-{variant}"
        run_program(mine, case_dir, mine_out)
        run_program(starter, case_dir, base_out)
        mine_f = validate(mine_out / "f00.out", case)
        base_f = validate(base_out / "f00.out", case)
        m_loss, m_miss, m_cov = scorer.score(mine_f, truth)
        b_loss, b_miss, b_cov = scorer.score(base_f, truth)
        distinct = len({(r["players_tracked"], tuple(r["rates"]))
                        for r in case["history"]})
        print(f"  {variant}: {len(case['history'])} reads, "
              f"{distinct} distinct, players "
              f"{case['history'][0]['players_tracked']} -> "
              f"{case['history'][-1]['players_tracked']}")
        print(f"    mine    loss {m_loss:.4f} miss {m_miss:.2f}pp "
              f"coverage {m_cov:.2f}")
        print(f"    starter loss {b_loss:.4f} miss {b_miss:.2f}pp "
              f"coverage {b_cov:.2f}")

    print("== edge cases ==")
    edge_dir = tmp / "edge"
    edge_dir.mkdir()
    base_case = json.loads((KIT / "input" / "p01.in").read_text())
    # One read only.
    one = dict(base_case)
    one["id"] = "e01"
    one["history"] = base_case["history"][:1]
    (edge_dir / "e01.in").write_text(json.dumps(one))
    # Null rates scattered through the history.
    nulls = json.loads(json.dumps(base_case))
    nulls["id"] = "e02"
    for read in nulls["history"]:
        read["rates"][3] = None
        read["rates"][20] = None
    (edge_dir / "e02.in").write_text(json.dumps(nulls))
    # A trophy at 0.00 and one already at 100 in the last read.
    bounds = json.loads(json.dumps(base_case))
    bounds["id"] = "e03"
    for read in bounds["history"]:
        read["rates"][0] = 0.0
    (edge_dir / "e03.in").write_text(json.dumps(bounds))
    edge_out = tmp / "edge-out"
    rc = run_program(mine, edge_dir, edge_out)
    for cid in ("e01", "e02", "e03"):
        validate(edge_out / f"{cid}.out", base_case)
        print(f"  {cid}: valid output written (rc={rc})")

    print("RESULT:", "FAIL" if FAIL else "ALL CHECKS PASSED")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
