#!/usr/bin/env python3
"""Run the randomized evaluation and generate plot images."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
REPETITIONS, TIME_LIMIT = 20, 600
BASE_SEED = 20261104
FRAME_SIZES = (64, 128, 256, 512, 1000)
PACKET_SIZES = tuple(size - 6 for size in FRAME_SIZES)
FILE_CHUNK_SIZES = tuple(size - 3 for size in PACKET_SIZES)
REFERENCE_FRAME_SIZE = 512
REFERENCE_PACKET_SIZE = REFERENCE_FRAME_SIZE - 6
REFERENCE_CHUNK_SIZE = REFERENCE_PACKET_SIZE - 3
INPUT_DATA = bytes(range(256)) * 64
MAX_PAYLOAD_SIZE = 1000
SWEEPS = {"fer": "fer", "delay": "a", "baud": "capacity", "packet_size": "frame-size"}


def configurations(sweep=None):
    # C (bit/s), requested one-way propagation (us), FILE_CHUNK_SIZE, FER.
    clean = ([(9600, 0, payload, 0.0) for payload in FILE_CHUNK_SIZES]
             + [(capacity, 0, REFERENCE_CHUNK_SIZE, 0.0) for capacity in (1800, 2400, 4800, 9600, 19200, 38400)])
    sweeps = ([(9600, 0, REFERENCE_CHUNK_SIZE, fer) for fer in (0, 0.025, 0.05, 0.1, 0.15, 0.2)]
              + [(9600, delay, REFERENCE_CHUNK_SIZE, 0.0) for delay in (0, 10000, 25000, 50000, 100000)]
              )
    configs = list(dict.fromkeys(clean + sweeps))
    if sweep is None:
        return configs
    predicates = {
        "fer": lambda c: c[0] == 9600 and c[1] == 0 and c[2] == REFERENCE_CHUNK_SIZE,
        "delay": lambda c: c[0] == 9600 and c[2] == REFERENCE_CHUNK_SIZE and c[3] == 0,
        "baud": lambda c: c[1] == 0 and c[2] == REFERENCE_CHUNK_SIZE and c[3] == 0,
        "packet_size": lambda c: c[0] == 9600 and c[1] == 0
                         and c[2] in FILE_CHUNK_SIZES and c[3] == 0,
    }
    return [c for c in configs if predicates[sweep](c)]


def repetitions_for(config):
    return REPETITIONS


def seed_for(config, repetition, base_seed=None):
    if base_seed is None:
        base_seed = BASE_SEED
    key = f"{base_seed}:{config}:{repetition}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big")


def ber_for_fer(fer, frame_bytes):
    if not 0 <= fer < 1 or frame_bytes <= 0:
        raise ValueError("FER must be in [0, 1) and frame length must be positive")
    return -math.expm1(math.log1p(-fer) / (8 * frame_bytes))


def replace_once(source, old, new):
    # shortcut: hooks match source text; update the anchors after protocol refactors.
    if source.count(old) != 1:
        raise ValueError(f"Evaluation hook no longer matches source: {old!r}")
    return source.replace(old, new, 1)


def instrument(source):
    hooks = r'''
#include <time.h>
static double eval_start, eval_end;
static double eval_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
'''
    source = replace_once(source, '#include <string.h>', '#include <string.h>\n' + hooks)
    source = replace_once(source, '    unsigned char okResponse =',
                          '    if (bufSize > 0 && buf[0] == 2) {\n'
                          '        if (eval_start == 0) eval_start = eval_now();\n'
                          '        printf("EVAL_FRAME %d %d\\n", bufSize - 3, n);\n'
                          '    } else if (bufSize > 0 && (buf[0] == 1 || buf[0] == 3)) {\n'
                          '        printf("EVAL_CONTROL %d %d\\n", buf[0], n);\n'
                          '    }\n    unsigned char okResponse =')
    source = replace_once(source, '            txSequence ^= 1;',
                          '            if (bufSize > 0 && buf[0] == 2) eval_end = eval_now();\n'
                          '            txSequence ^= 1;')
    return replace_once(source, 'int llCloseTx()\n{',
                         'int llCloseTx()\n{\n'
                         '    printf("EVAL_DATA %.9f\\n", eval_end - eval_start);')


def launch(command):
    process = subprocess.Popen(["stdbuf", "-oL", *map(str, command)], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=True)
    lines = []

    def drain():
        with process.stdout:
            for line in process.stdout:
                if not re.search(r"socat\[\d+\] [NI] ", line):
                    lines.append(line)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    return process, lines, reader


def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    process.stdin.close()


def wait_line(process, lines, pattern, deadline, offset=0):
    while time.monotonic() < deadline:
        for line in lines[offset:]:
            offset += 1
            match = re.search(pattern, line)
            if match:
                return match
        if process.poll() is not None:
            raise RuntimeError(f"Process exited while waiting for {pattern!r}: {''.join(lines[-4:])}")
        time.sleep(0.01)
    raise TimeoutError(f"Timed out waiting for {pattern!r}")


def cable_command(cable, lines, command, pattern=r"PROPAGATION DELAY SET TO (\d+) usec"):
    offset = len(lines)
    cable.stdin.write(command + "\n")
    cable.stdin.flush()
    match = wait_line(cable, lines, pattern,
                      time.monotonic() + 10, offset)
    return float(match.group(1))


def transfer(folder, config, repetition, actual_delay, ber, reference_frame_bytes, cancelled=None):
    capacity, delay, chunk_size, fer = config
    source, destination = folder / "sample.bin", folder / "output.bin"
    data = INPUT_DATA
    source.write_bytes(data)
    destination.unlink(missing_ok=True)
    deadline = time.monotonic() + TIME_LIMIT
    processes = []
    started = time.monotonic()
    status, tx_lines = "failed", []
    try:
        rx, rx_lines, rx_reader = launch([folder / "main", folder / "rx", 9600, "rx", destination])
        processes.append(rx)
        wait_line(rx, rx_lines, re.escape(f"Serial port {folder / 'rx'} opened"), deadline)
        started = time.monotonic()
        tx, tx_lines, tx_reader = launch([folder / "main", folder / "tx", 9600, "tx", source])
        processes.append(tx)
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError("Evaluation cancelled")
            if tx.poll() is not None:
                tx_reader.join(timeout=0.05)
            if rx.poll() is not None:
                rx_reader.join(timeout=0.05)
            if any("File transfer failed." in line for line in tx_lines):
                break
            if tx.poll() is not None and not any("File packets complete." in line for line in tx_lines):
                break
            if rx.poll() is not None and not any("File reception complete:" in line for line in rx_lines):
                break
            if tx.poll() is not None and rx.poll() is not None:
                tx_reader.join(timeout=max(0, deadline - time.monotonic()))
                rx_reader.join(timeout=max(0, deadline - time.monotonic()))
                if tx_reader.is_alive() or rx_reader.is_alive():
                    status = "timeout"
                    break
                if (tx.returncode == rx.returncode == 0 and destination.exists()
                        and destination.read_bytes() == data
                        and any("File packets complete." in line for line in tx_lines)
                        and any("File reception complete:" in line for line in rx_lines)):
                    status = "ok"
                break
            time.sleep(0.01)
        else:
            status = "timeout"
    except TimeoutError:
        status = "timeout"
    finally:
        elapsed = time.monotonic() - started
        for process in processes:
            stop(process)
    frames = [tuple(map(int, line.split()[1:])) for line in tx_lines if line.startswith("EVAL_FRAME ")]
    intervals = [float(line.split()[1]) for line in tx_lines if line.startswith("EVAL_DATA ")]
    controls = {int(line.split()[1]): int(line.split()[2]) for line in tx_lines
                if line.startswith("EVAL_CONTROL ")}
    duration = intervals[-1] if status == "ok" and intervals else None
    if status == "ok":
        assert duration and duration > 0 and sum(k for k, _ in frames) == len(data)
    return dict(capacity_bps=capacity, endpoint_baud=9600, requested_prop_us=delay,
                actual_prop_us=actual_delay, max_payload=MAX_PAYLOAD_SIZE, payload_bytes=chunk_size,
                nominal_frame_bytes=chunk_size + 9,
                fer=fer, ber=ber, reference_frame_bytes=reference_frame_bytes,
                repetition=repetition, seed=seed_for(config, repetition), input_file=source.name,
                file_bytes=len(data), status=status, transfer_s=elapsed, data_s=duration or "",
                mean_frame_bytes=sum(w for _, w in frames) / len(frames) if frames else "",
                start_frame_bytes=controls.get(1, ""), end_frame_bytes=controls.get(3, ""),
                full_frame_bytes=(sum(full) / len(full) if (full := [w for k, w in frames
                                  if k == chunk_size]) else ""))


def split_results(results):
    with results.open() as file:
        reader = csv.DictReader(file)
        fields, rows = reader.fieldnames, list(reader)
    for sweep in SWEEPS:
        selected = set(configurations(sweep))
        with (HERE / f"results-{sweep}.csv").open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(row for row in rows if configuration_key(row) in selected)


def private_cable_source(folder):
    source = (ROOT / "cable/cable.c").read_text()
    source = replace_once(source, '    int STOP = FALSE;',
                          '    unsigned int eval_tx_seed = 1, eval_rx_seed = 2;\n'
                          '    int STOP = FALSE;')
    source = replace_once(source, '            else if (strncmp(rxStdin, "ber ", 4) == 0)',
                          '            else if (strncmp(rxStdin, "seed ", 5) == 0) {\n'
                          '                unsigned int seed;\n'
                          '                if (sscanf(rxStdin + 5, "%u", &seed) == 1) {\n'
                          '                    eval_tx_seed = seed;\n'
                          '                    eval_rx_seed = seed ^ 0x9e3779b9U;\n'
                          '                    printf("SEED SET TO %u\\n", seed);\n'
                          '                }\n'
                          '            }\n'
                          '            else if (strncmp(rxStdin, "ber ", 4) == 0)')
    # Independent streams keep faults per direction independent of thread scheduling.
    if source.count('rand()') != 4:
        raise ValueError("Cable random-number hooks no longer match")
    for direction in ('tx', 'tx', 'rx', 'rx'):
        source = source.replace('rand()', f'rand_r(&eval_{direction}_seed)', 1)
    for macro, port in (("TXDEV", "tx"), ("RXDEV", "rx"),
                        ("TX_EMULATOR", "tx-link"), ("RX_EMULATOR", "rx-link")):
        source, count = re.subn(rf'(?m)^#define {macro} "[^"\n]+"$',
                                f'#define {macro} "{folder / port}"', source)
        if count != 1:
            raise ValueError(f"Cannot isolate cable port {macro}")
    return replace_once(source, '    system("killall socat");',
                        '    // The evaluation stops only this cable process group.')


def run_configurations(configs, results, label, cancelled, references, repetitions):
    total = len(repetitions) * len(configs)
    completed = 0
    cable = None
    try:
        with tempfile.TemporaryDirectory(prefix="rc-eval-", dir="/tmp") as temporary:
            folder = Path(temporary)
            link = folder / "link_layer_eval.c"
            link.write_text(instrument((ROOT / "src/link_layer.c").read_text()))
            cable_source = folder / "cable.c"
            cable_source.write_text(private_cable_source(folder))
            subprocess.run(["gcc", "-Wall", str(cable_source), "-o", str(folder / "cable")], check=True)
            cable, cable_lines, cable_reader = launch([folder / "cable"])
            wait_line(cable, cable_lines, "Cable ready", time.monotonic() + 10)
            with results.open("w", newline="") as file:
                writer = None
                reference_lengths = dict(references or {})
                for config in configs:
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("Evaluation cancelled")
                    capacity, delay, chunk_size, fer = config
                    subprocess.run(["gcc", "-Wall", f"-DFILE_CHUNK_SIZE={chunk_size}",
                                    "-I", str(ROOT / "src"),
                                    *(str(ROOT / "src" / name) for name in
                                      ("main.c", "application_layer.c", "serial_port.c")),
                                    str(link), "-o", str(folder / "main")], check=True)
                    reference = reference_lengths.get(chunk_size, 0)
                    if fer and not reference:
                        raise RuntimeError("A successful clean reference is required before error trials")
                    ber = ber_for_fer(fer, reference) if fer else 0.0
                    cable_command(cable, cable_lines, f"ber {ber:.17g}", r"BER SET TO ([0-9.]+)")
                    cable_command(cable, cable_lines, f"baud {capacity}")
                    actual_delay = cable_command(cable, cable_lines, f"prop {delay}")
                    for repetition in repetitions:
                        seed = seed_for(config, repetition)
                        cable_command(cable, cable_lines, f"seed {seed}", r"SEED SET TO (\d+)")
                        completed += 1
                        print(f"[{label} {completed}/{total}] C={capacity}, "
                              f"delay={delay / 1000:g}ms, L={chunk_size + 9}, CHUNK={chunk_size}, FER={fer:.1%}, seed={seed}", flush=True)
                        row = transfer(folder, config, repetition, actual_delay, ber, reference, cancelled)
                        if not fer and row["status"] == "ok" and row["full_frame_bytes"]:
                            reference_lengths.setdefault(chunk_size, row["full_frame_bytes"])
                        row["cable_unreliable"] = int(any("UNRELIABLE RATE" in s for s in cable_lines))
                        if writer is None:
                            writer = csv.DictWriter(file, fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                        file.flush()
                        print(f"[{label}] {row['status']}: {row['transfer_s']:.2f}s", flush=True)
    finally:
        if cable is not None:
            # Avoid cable's quit handler: it runs killall socat outside this experiment.
            stop(cable)
            cable_reader.join(timeout=2)


def configuration_key(row):
    return (int(row["capacity_bps"]), int(row["requested_prop_us"]),
            int(row["payload_bytes"]), float(row["fer"]))


def parallel_jobs(configs):
    jobs, assigned = [], set()
    for sweep in SWEEPS:
        selected = [c for c in configurations(sweep) if c in configs and c not in assigned]
        assigned.update(selected)
        if sweep in ("baud", "packet_size"):
            for capacity, chunk in dict.fromkeys((c[0], c[2]) for c in selected):
                jobs.append((f"{sweep}-{capacity}-{chunk}",
                             [c for c in selected if (c[0], c[2]) == (capacity, chunk)]))
        elif selected:
            jobs.append((sweep, selected))
    # All trials send the same file; inverse capacity approximates serialization cost.
    return sorted(jobs, key=lambda job: sum(1 / c[0] for c in job[1]), reverse=True)


def run_parallel(configs, results, previous_rows=(), workers=16):
    cancelled = threading.Event()
    complete, references = {}, {}
    for config in configs:
        rows = [r for r in previous_rows if configuration_key(r) == config]
        if len(rows) == repetitions_for(config) and {int(r["repetition"]) for r in rows} == set(range(1, repetitions_for(config) + 1)):
            complete[config] = rows
        for row in rows:
            if row["status"] == "ok" and row["full_frame_bytes"]:
                references.setdefault(config[2], float(row["full_frame_bytes"]))
    pending = [c for c in configs if c not in complete]
    with tempfile.TemporaryDirectory(prefix="rc-eval-results-") as temporary:
        parts = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            try:
                for label, selected in parallel_jobs(pending):
                    for repetition in range(1, max(repetitions_for(c) for c in selected) + 1):
                        trial_configs = [c for c in selected if repetition <= repetitions_for(c)]
                        trial_label = f"{label}-r{repetition}"
                        part = Path(temporary) / f"{trial_label}.csv"
                        parts.append(part)
                        futures.append(pool.submit(run_configurations, trial_configs, part, trial_label,
                                                   cancelled, references, (repetition,)))
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                cancelled.set()
                raise
        rows = [row for group in complete.values() for row in group]
        for part in parts:
            with part.open() as source:
                rows.extend(csv.DictReader(source))
        rows.sort(key=lambda r: (configs.index(configuration_key(r)), int(r["repetition"])))
        with results.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main():
    for tool in ("gcc", "socat", "stdbuf"):
        if not shutil.which(tool):
            raise RuntimeError(f"Missing required tool: {tool}")
    if importlib.util.find_spec("matplotlib") is None:
        raise RuntimeError("Install Matplotlib before running the evaluation")
    configs = configurations()
    assert len(configs) == len(set(configs))
    results = HERE / "results.csv"
    previous = []
    if results.exists():
        with results.open() as file:
            previous = [r for r in csv.DictReader(file)
                        if r.get("seed") and r.get("start_frame_bytes") and r.get("end_frame_bytes")
                        and r.get("nominal_frame_bytes") == str(int(r["payload_bytes"]) + 9)
                        and r.get("max_payload") == str(MAX_PAYLOAD_SIZE)
                        and r.get("file_bytes") == str(len(INPUT_DATA))
                        and r.get("input_file") == "sample.bin"
                        and int(r["seed"]) == seed_for(configuration_key(r), int(r["repetition"]))]
    run_parallel(configs, results, previous)
    split_results(results)
    subprocess.run([sys.executable, str(HERE / "plot.py")], check=True)
    print(f"Finished: {results}", flush=True)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    main()
