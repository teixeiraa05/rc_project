"""Exercise worker isolation with concurrent real cable transfers."""
import contextlib
import csv
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import run


class ParallelChecks(unittest.TestCase):
    def test_concurrent_transfers_preserve_shared_files(self):
        configs = [(9600, 0, 512, 0), (9600, 0, 512, .05),
                   (9600, 10000, 512, 0),
                   (4800, 0, 512, 0), (4800, 0, 512, .05),
                   (19200, 0, 512, 0), (19200, 0, 512, .05),
                   (38400, 0, 512, 0), (38400, 0, 512, .05),
                   (9600, 0, 61, 0), (9600, 0, 61, .05),
                   (9600, 0, 125, 0), (9600, 0, 125, .05),
                   (9600, 0, 253, 0), (9600, 0, 253, .05)]
        shared = [run.ROOT / name for name in ('src/link_layer.h', 'bin/main', 'bin/cable')]
        original = {p: p.read_bytes() for p in shared if p.exists()}
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / 'results.csv'
            started = time.monotonic()
            with patch.object(run, 'REPETITIONS', 1), patch.object(run, 'FER_REPETITIONS', 1), \
                    contextlib.redirect_stdout(io.StringIO()):
                run.run_parallel(configs, results)
            elapsed = time.monotonic() - started
            with results.open() as file:
                rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), len(configs))
        self.assertTrue(all(row['status'] == 'ok' for row in rows))
        self.assertTrue(all(int(row['max_payload']) == 1000 for row in rows))
        self.assertLess(elapsed, sum(float(row['transfer_s']) for row in rows))
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_worker_failure_cancels_peers_and_preserves_results(self):
        def worker(configs, results, label, cancelled, references, repetitions):
            if label.startswith('baud-1800-'):
                raise RuntimeError('worker failed')
            self.assertTrue(cancelled.wait(timeout=5))

        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / 'results.csv'
            results.write_text('previous results\n')
            with patch.object(run, 'run_configurations', side_effect=worker):
                with self.assertRaisesRegex(RuntimeError, 'worker failed'):
                    run.run_parallel(run.configurations(), results)
            self.assertEqual(results.read_text(), 'previous results\n')

    def test_completed_trials_are_reused_with_their_reference(self):
        clean, noisy = (9600, 0, 512, 0), (9600, 0, 512, .05)
        previous = [dict(capacity_bps=9600, requested_prop_us=0, payload_bytes=512,
                         fer=0, repetition=n, status='ok', full_frame_bytes=525)
                    for n in range(1, 21)]

        def worker(configs, results, label, cancelled, references, repetitions):
            self.assertEqual(configs, [noisy])
            self.assertEqual(references, {512: 525})
            with results.open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=list(previous[0]))
                writer.writeheader()
                writer.writerows(dict(previous[n - 1], fer=.05) for n in repetitions)

        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / 'results.csv'
            with patch.object(run, 'run_configurations', side_effect=worker) as execute:
                run.run_parallel([clean, noisy], results, previous)
                self.assertEqual(execute.call_count, 20)
            with results.open() as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 40)
            self.assertEqual([float(row['fer']) for row in rows], [0] * 20 + [.05] * 20)

    def test_removed_run_options_are_rejected(self):
        for args in (['--parallel'], ['--workers', '8'], ['fer']):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, '-B', str(run.HERE / 'run.py'), *args],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('unrecognized arguments', result.stderr)

    def test_balanced_jobs_cover_every_configuration_once(self):
        jobs = run.parallel_jobs(run.configurations())
        flattened = [config for _, configs in jobs for config in configs]
        self.assertEqual(len(flattened), 30)
        self.assertEqual(set(flattened), set(run.configurations()))
        self.assertEqual(len(jobs), 12)
        self.assertEqual({c[0] for c in jobs[0][1]}, {1800})
        for label, configs in jobs:
            if label.startswith('baud-'):
                self.assertEqual(len({c[0] for c in configs}), 1)
            for index, config in enumerate(configs):
                if config[3]:
                    self.assertTrue(any(c[2] == config[2] and c[3] == 0
                                        for c in configs[:index]))

    def test_scheduler_runs_twenty_fer_trials_and_five_other_trials(self):
        def worker(configs, results, label, cancelled, references, repetitions):
            with results.open('w', newline='') as file:
                fields = ('capacity_bps', 'requested_prop_us', 'payload_bytes', 'fer', 'repetition')
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                for config in configs:
                    for repetition in repetitions:
                        writer.writerow(dict(zip(fields, (*config, repetition))))

        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / 'results.csv'
            with patch.object(run, 'run_configurations', side_effect=worker), \
                    patch.object(run, 'ThreadPoolExecutor', wraps=run.ThreadPoolExecutor) as pool:
                run.run_parallel(run.configurations(), results)
            pool.assert_called_once_with(max_workers=8)
            with results.open() as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 240)
            for config in run.configurations():
                trials = [r for r in rows if run.configuration_key(r) == config]
                self.assertEqual(len(trials), run.repetitions_for(config))
                self.assertEqual({int(r['repetition']) for r in trials},
                                 set(range(1, run.repetitions_for(config) + 1)))

    def test_private_cable_has_no_global_cleanup(self):
        source = run.private_cable_source(Path('/tmp/rc-eval-check'))
        self.assertNotIn('killall socat', source)
        for port in ('tx', 'rx', 'tx-link', 'rx-link'):
            self.assertIn(f'"/tmp/rc-eval-check/{port}"', source)
        self.assertNotIn('"/tmp/ttyS10"', source)


if __name__ == '__main__':
    unittest.main()
