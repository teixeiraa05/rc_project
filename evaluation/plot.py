#!/usr/bin/env python3
"""Regenerate plot images from results.csv."""

import argparse
import csv
from collections import Counter, defaultdict
import json
from pathlib import Path
from run import (configurations, repetitions_for, SWEEPS, MAX_PAYLOAD_SIZE,
                 FILE_CHUNK_SIZES, REFERENCE_CHUNK_SIZE, configuration_key, seed_for)
import math
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "report/assets"


def theoretical_efficiencies(row):
    capacity = float(row["capacity_bps"])
    frame_bytes = float(row["mean_frame_bytes"])
    propagation = float(row["actual_prop_us"]) / 1e6
    fer = float(row["fer"])
    file_bytes = int(row["file_bytes"])
    chunk_bytes = int(row["payload_bytes"])
    start_bytes = float(row["start_frame_bytes"])
    end_bytes = float(row["end_frame_bytes"])
    if (not all(math.isfinite(v) for v in (capacity, frame_bytes, propagation, fer))
            or capacity <= 0 or frame_bytes <= 0 or propagation < 0 or not 0 <= fer < 1
            or file_bytes <= 0 or chunk_bytes <= 0
            or not all(math.isfinite(v) and v >= 6 for v in (start_bytes, end_bytes))):
        raise ValueError("Invalid Stop-and-Wait model parameters")
    frames = math.ceil(file_bytes / chunk_bytes)
    # RR and REJ supervision frames contain five bytes.
    cycle = 10 * (frame_bytes + 5) / capacity + 2 * propagation
    opening = 100 / capacity + 2 * propagation
    closing = 150 / capacity + 3 * propagation
    start = 10 * (start_bytes + 5) / capacity + 2 * propagation
    end = 10 * (end_bytes + 5) / capacity + 2 * propagation
    information = start + frames * cycle + end
    clean_time = opening + information + closing
    # Ideal class model: clean handshakes, equal-duration attempts, unlimited retries.
    predicted_time = opening + information / (1 - fer) + closing
    clean = 8 * file_bytes / (capacity * clean_time)
    return 8 * file_bytes / (capacity * predicted_time), clean


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[configuration_key(row)].append(row)
    result = []
    for (capacity, delay, chunk_size, fer), trials in sorted(grouped.items()):
        good = [r for r in trials if r["status"] == "ok"]
        g = dict(capacity=capacity, delay=delay, chunk_size=chunk_size, fer=fer,
                 count=len(trials), successful=len(good), mean_s=None, throughput=None, efficiency=None, error=0,
                 theory=None, error_free=None, propagation_ms=None, frame_bytes=None)
        if good:
            times = [float(r["transfer_s"]) for r in good]
            bits = sum(8 * int(r["file_bytes"]) for r in good)
            g["mean_s"] = statistics.mean(times)
            g["throughput"] = bits / sum(times)
            g["efficiency"] = g["throughput"] / capacity
            g["propagation_ms"] = statistics.mean(float(r["actual_prop_us"]) / 1000 for r in good)
            g["frame_bytes"] = statistics.mean(float(r["mean_frame_bytes"]) for r in good)
            references = [theoretical_efficiencies(r) for r in good]
            # Aggregate predicted times with the same useful-bit weighting as measurements.
            g["theory"] = bits / sum(8 * int(r["file_bytes"]) / (capacity * s)
                                      for r, (s, _) in zip(good, references)) / capacity
            g["error_free"] = bits / sum(8 * int(r["file_bytes"]) / (capacity * s0)
                                          for r, (_, s0) in zip(good, references)) / capacity
            if len(good) > 1:
                g["error"] = (g["efficiency"] * statistics.stdev(times)
                              / math.sqrt(len(times)) / g["mean_s"])
        result.append(g)
    return result


def curve(ax, groups, x):
    groups = sorted([g for g in groups if g["efficiency"] is not None], key=x)
    if groups:
        xs = [x(g) for g in groups]
        ax.errorbar(xs, [100 * g["efficiency"] for g in groups],
                    yerr=[100 * g["error"] for g in groups], fmt="o-", capsize=3,
                    color="#0072B2", label="Measured")
        ax.plot(xs, [100 * g["theory"] for g in groups], "--",
                color="#D55E00", label="Theoretical")
    return groups


def plots(groups, names=("capacity", "frame-size", "a", "fer")):
    ASSETS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": 0.2})
    baseline = [g for g in groups if g["capacity"] == 9600 and g["chunk_size"] == REFERENCE_CHUNK_SIZE]
    for name in names:
        fig, ax = plt.subplots(figsize=(4.5, 3))
        if name == "fer":
            selected = [g for g in baseline if g["delay"] == 0]
            x = lambda g: g["fer"]
            ax.set(xlabel=r"Target FER ($p_e$)", ylim=(45, 103))
        elif name == "a":
            selected = [g for g in baseline if g["fer"] == 0]
            x = lambda g: g["propagation_ms"]
            ax.set(xlabel=r"Propagation delay $T_{\mathrm{prop}}$ (ms)", ylim=(40, 103))
        else:
            selected = [g for g in groups if g["delay"] == 0 and g["fer"] == 0]
            if name == "capacity":
                selected = [g for g in selected if g["chunk_size"] == REFERENCE_CHUNK_SIZE]
                x = lambda g: g["capacity"]
                ax.set(xlabel=r"Link capacity $C$ (bit/s)", ylim=(72, 80))
                capacities = sorted({x(g) for g in selected})
                ax.set_xscale("log", base=2)
                ax.set_xticks(capacities, labels=[f"{c:,}" for c in capacities], rotation=45, ha="right")
                ax.minorticks_off()
            else:
                selected = [g for g in selected if g["capacity"] == 9600
                            and g["chunk_size"] in FILE_CHUNK_SIZES]
                x = lambda g: g["chunk_size"] + 9
                ax.set(xlabel=r"Frame size $L$ (bytes, before stuffing)", ylim=(0, 103))
                sizes = sorted(x(g) for g in selected)
                ax.set_xticks(sizes, labels=[f"{size:.1f}".removesuffix(".0") for size in sizes], rotation=45, ha="right")
        curve(ax, selected, x)
        ax.set_ylabel(r"Efficiency $S$ (%)")
        ax.legend(fontsize=8, loc="lower left")
        fig.tight_layout()
        fig.savefig(ASSETS / f"efficiency-{name}.svg", bbox_inches="tight")
        plt.close(fig)


def write_report_metadata(rows, groups):
    groups = {tuple(g[k] for k in ("capacity", "delay", "chunk_size", "fer")): g
              for g in groups}
    expected = set(configurations())
    current = Counter(map(configuration_key, rows)) == {c: repetitions_for(c) for c in expected}
    values = {"total": len(rows), "passed": sum(r["status"] == "ok" for r in rows),
              "current": current, "planned_total": sum(repetitions_for(c) for c in expected)}
    cases = {"baseline": (9600, 0, REFERENCE_CHUNK_SIZE, 0), "low": (1800, 0, REFERENCE_CHUNK_SIZE, 0),
             "high": (38400, 0, REFERENCE_CHUNK_SIZE, 0), "small": (9600, 0, FILE_CHUNK_SIZES[0], 0),
             "large": (9600, 0, FILE_CHUNK_SIZES[-1], 0), "delay": (9600, 100000, REFERENCE_CHUNK_SIZE, 0),
             "fer": (9600, 0, REFERENCE_CHUNK_SIZE, .2)}
    for name, config in cases.items():
        group = groups.get(config)
        good = group is not None and group["efficiency"] is not None
        values[name + "_eff"] = f"{100 * group['efficiency']:.1f}%" if good else "—"
        values[name + "_rate"] = f"{config[0] * group['efficiency'] / 1000:.2f}" if good else "—"
        values[name + "_theory"] = f"{100 * group['theory']:.1f}%" if good else "—"
    baseline = groups.get(cases["baseline"])
    values["baseline_gap"] = (f"{100 * (baseline['theory'] - baseline['efficiency']):.1f}"
                              if baseline and baseline['efficiency'] is not None else "—")
    changes = []
    for field in ("data_s", "transfer_s"):
        means = []
        for name in ("baseline", "delay"):
            times = [float(r[field]) for r in rows if configuration_key(r) == cases[name]
                     and r["status"] == "ok" and r.get(field)]
            means.append(statistics.mean(times) if times else None)
        changes.append(means[1] - means[0] if None not in means else None)
    for name, change in zip(("data_delta", "full_delta", "outside_delta"),
                            (*changes, changes[1] - changes[0] if None not in changes else None)):
        values[name] = f"{change:.2f}" if change is not None else "—"
    lines = ["// Generated from evaluation/results.csv; do not edit.", "#let results = ("]
    lines.extend(f"  {key}: {json.dumps(value, ensure_ascii=False)}," for key, value in values.items())
    lines.append(")")
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "results-meta.typ").write_text("\n".join(lines) + "\n")


def write_summary_table(rows, groups):
    grouped = {tuple(g[k] for k in ("capacity", "delay", "chunk_size", "fer")): g for g in groups}
    cases = [
        ("Reference", (9600, 0, REFERENCE_CHUNK_SIZE, 0)),
        ("38,400 bit/s", (38400, 0, REFERENCE_CHUNK_SIZE, 0)),
        ("64-byte frame", (9600, 0, FILE_CHUNK_SIZES[0], 0)),
        ("25 ms added delay", (9600, 25000, REFERENCE_CHUNK_SIZE, 0)),
        ("100 ms added delay", (9600, 100000, REFERENCE_CHUNK_SIZE, 0)),
        ("20% target FER", (9600, 0, REFERENCE_CHUNK_SIZE, .2)),
    ]
    cells = []
    for label, config in cases:
        group = grouped.get(config)
        if group is None:
            raise ValueError(f"Missing summary configuration: {config}")
        if group["successful"]:
            mean = f"{group['mean_s']:.2f}"
            throughput = f"{group['throughput'] / 1000:.2f}"
            efficiency = f"{100 * group['efficiency']:.1f}%"
        else:
            mean = throughput = efficiency = "—"
        cells.append(f"    [{label}], [{mean}], [{throughput}], [{efficiency}], "
                     f"[{group['successful']} / {group['count']}],")
    successful = sum(row["status"] == "ok" for row in rows)
    source = """// Generated by evaluation/plot.py from evaluation/results.csv.
#figure(
  table(
    columns: (2fr, 1fr, 1.2fr, 1fr, 1fr),
    align: (left, center, center, center, center),
    inset: (x: 6pt, y: 4pt),
    stroke: 0.4pt,
    fill: (_, row) => if row == 0 { rgb("#eeeeee") },
    table.header([*Setting*], [*Time (s)*], [*$R$ (kbit/s)*], [*$S$*], [*OK / runs*]),
""" + "\n".join(cells) + f"""
  ),
  kind: table,
  caption: [Representative results, averaged over successful trials.
    Across all configurations, {successful} of {len(rows)} transfers completed correctly.
    Reference: 9,600 bit/s, 512-byte frame before stuffing, no added propagation delay, FER 0%.],
) <results-summary>
"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "results-summary.typ").write_text(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sweep", nargs="?", choices=SWEEPS)
    sweep = parser.parse_args().sweep
    results = HERE / (f"results-{sweep}.csv" if sweep else "results.csv")
    with results.open() as file:
        rows = list(csv.DictReader(file))
    if any(int(row["max_payload"]) != MAX_PAYLOAD_SIZE for row in rows):
        raise ValueError("Results used a different link-layer limit; rerun with fixed MAX_PAYLOAD_SIZE")
    if any(int(row["seed"]) != seed_for(configuration_key(row), int(row["repetition"]))
           for row in rows):
        raise ValueError("Results contain an unexpected trial seed")
    expected = set(configurations(sweep))
    actual = set(map(configuration_key, rows))
    if len(rows) != sum(repetitions_for(c) for c in expected) or actual != expected:
        raise ValueError("Expected current profiles with 20 trials per configuration; rerun the evaluation")
    for config in expected:
        trials = [r for r in rows if configuration_key(r) == config]
        if len(trials) != repetitions_for(config) or {int(r["repetition"]) for r in trials} != set(range(1, repetitions_for(config) + 1)):
            raise ValueError(f"Incorrect repetitions for configuration {config}")
    if ({r["input_file"] for r in rows} != {"sample.bin"}
            or len({r["file_bytes"] for r in rows}) != 1):
        raise ValueError("Every trial must use sample.bin with the same byte count")
    groups = summarize(rows)
    if sweep is None:
        write_summary_table(rows, groups)
        write_report_metadata(rows, groups)
    assert all(0 <= g["theory"] <= g["error_free"] <= 1
               for g in groups if g["efficiency"] is not None)
    names = (SWEEPS[sweep],) if sweep else tuple(SWEEPS.values())
    plots(groups, names)
    print(f"Generated {len(names)} plot(s) in {ASSETS}")


if __name__ == "__main__":
    main()
