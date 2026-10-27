"""Small checks for evaluation profiles, noise conversion and serial efficiency."""
import math
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import plot

from run import ber_for_fer, configurations, instrument, ROOT, repetitions_for
from plot import summarize, theoretical_efficiencies


class EvaluationChecks(unittest.TestCase):
    def test_profiles(self):
        configs = configurations()
        self.assertEqual(len(configs), 30)
        self.assertEqual(len(set(configs)), 30)
        baseline = [c for c in configs if c[0] == 9600 and c[2] == 512]
        self.assertEqual({c[3] for c in baseline if c[1] == 0},
                         {0, .025, .05, .1, .15, .2})
        self.assertEqual({c[1] for c in baseline if c[3] == 0},
                         {0, 10000, 25000, 50000, 100000})
        for fer in (0, .05):
            self.assertEqual({c[2] for c in configs
                              if c[0] == 9600 and c[1] == 0 and c[3] == fer and c[2] != 512},
                             {61, 125, 253, 509, 997})
        for index, (_, _, maximum, fer) in enumerate(configs):
            if fer:
                self.assertTrue(any(c[2] == maximum and c[3] == 0 for c in configs[:index]))

    def test_independent_sweeps(self):
        expected_counts = {"fer": 6, "delay": 5, "baud": 12, "packet_size": 10}
        combined = set()
        for sweep, count in expected_counts.items():
            configs = configurations(sweep)
            self.assertEqual(len(configs), count)
            combined.update(configs)
            for index, (_, _, maximum, fer) in enumerate(configs):
                if fer:
                    self.assertTrue(any(c[2] == maximum and c[3] == 0
                                        for c in configs[:index]))
        self.assertEqual(combined, set(configurations()))

    def test_fer_repetition_counts(self):
        self.assertEqual(sum(repetitions_for(c) for c in configurations()), 240)
        self.assertTrue(all(repetitions_for(c) == 20 for c in configurations("fer")))
        self.assertTrue(all(repetitions_for(c) == 5 for c in configurations()
                            if c not in configurations("fer")))
        self.assertEqual([sum(repetitions_for(c) for c in configurations(sweep))
                          for sweep in ("fer", "delay", "baud", "packet_size")], [120, 40, 90, 50])

    def test_noise_conversion(self):
        for length in (73, 525, 1017):
            for fer in (0, .025, .05, .1, .15, .2):
                ber = ber_for_fer(fer, length)
                self.assertAlmostEqual(-math.expm1(8 * length * math.log1p(-ber)), fer)
        self.assertLess(ber_for_fer(.05, 1000), ber_for_fer(.05, 64))
        for fer, length in ((-1, 525), (1, 525), (.05, 0)):
            with self.assertRaises(ValueError):
                ber_for_fer(fer, length)

    def test_ideal_reference_and_failed_trials(self):
        row = dict(capacity_bps=9600, requested_prop_us=0, max_payload=1000, payload_bytes=512,
                   fer=.05, ber=ber_for_fer(.05, 525), status='ok', transfer_s=5, file_bytes=4096,
                   actual_prop_us=0, mean_frame_bytes=525)
        group = summarize([row, dict(row, status='failed')])[0]
        self.assertEqual(group['count'], 2)
        self.assertEqual(group['frame_bytes'], 525)
        theory, clean = theoretical_efficiencies(row)
        self.assertAlmostEqual(group['error_free'], .8 * 512 / 530)
        self.assertAlmostEqual(theory, .95 * .8 * 512 / 530)
        self.assertAlmostEqual(group['theory'], theory)
        self.assertLess(theory, clean)
        self.assertAlmostEqual(group['efficiency'], 4096 * 8 / 5 / 9600)

    def test_prediction_uses_full_data_reply_cycle(self):
        row = dict(capacity_bps=9600, mean_frame_bytes=525, actual_prop_us=0,
                   fer=0, file_bytes=1000, payload_bytes=500)
        clean = 8000 / (2 * 10 * (525 + 5))
        self.assertEqual(theoretical_efficiencies(row), (clean, clean))
        self.assertAlmostEqual(theoretical_efficiencies(dict(row, fer=.05))[0], .95 * clean)
        self.assertEqual(theoretical_efficiencies(dict(row, capacity_bps=19200)), (clean, clean))
        delayed = dict(row, actual_prop_us=100000)
        expected = 8000 / (9600 * 2 * (5300 / 9600 + .2))
        self.assertAlmostEqual(theoretical_efficiencies(delayed)[0], expected)
        self.assertEqual(theoretical_efficiencies(dict(row, transfer_s=1e9)), (clean, clean))
        partial = dict(row, file_bytes=750, mean_frame_bytes=400)
        self.assertAlmostEqual(theoretical_efficiencies(partial)[0], 6000 / (2 * 4050))
        self.assertLess(theoretical_efficiencies(dict(row, payload_bytes=250, mean_frame_bytes=275))[0], clean)
        for changes in ({'capacity_bps': 0}, {'actual_prop_us': -1}, {'fer': -1},
                        {'fer': 1}, {'mean_frame_bytes': 0}, {'file_bytes': 0}, {'payload_bytes': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                theoretical_efficiencies(dict(row, **changes))

    def test_theory_uses_target_fer_directly(self):
        row = dict(capacity_bps=9600, mean_frame_bytes=525, actual_prop_us=0,
                   ber=.0001, fer=.2, file_bytes=1000, payload_bytes=500)
        theory, clean = theoretical_efficiencies(row)
        self.assertAlmostEqual(theory, .8 * clean)
        self.assertEqual(theoretical_efficiencies(dict(row, ber=0))[0], theory)
        self.assertAlmostEqual(theoretical_efficiencies(dict(row, fer=.05))[0], .95 * clean)

    def test_instrumentation_only_measures(self):
        source = instrument((ROOT / 'src/link_layer.c').read_text())
        self.assertIn('EVAL_FRAME', source)
        self.assertIn('EVAL_DATA', source)
        self.assertNotIn('EVAL_FIELD_ERROR', source)
        self.assertNotIn('EVAL_SEED', source)
        self.assertNotIn('rand(', source)

    def test_summary_table_excludes_failed_times_and_counts_all_trials(self):
        configs = [(9600, 0, 512, 0), (38400, 0, 512, 0), (9600, 0, 61, 0),
                   (9600, 25000, 512, 0), (9600, 100000, 512, 0), (9600, 0, 512, .2)]
        rows = [dict(capacity_bps=c, requested_prop_us=d, payload_bytes=p, fer=f, ber=ber_for_fer(f, p + 10),
                     status='ok', transfer_s=5, file_bytes=4096, actual_prop_us=d,
                     mean_frame_bytes=p + 10) for c, d, p, f in configs]
        rows += [dict(rows[0], transfer_s=10), dict(rows[0], transfer_s=100, status='failed')]
        with tempfile.TemporaryDirectory() as directory, patch('plot.ASSETS', Path(directory)):
            plot.write_summary_table(rows, summarize(rows))
            source = (Path(directory) / 'results-summary.typ').read_text()
            self.assertIn('[Reference], [7.50], [4.37], [45.5%], [2 / 3]', source)
            self.assertIn('7 of 8 transfers', source)
            rows[5]['status'] = 'failed'
            plot.write_summary_table(rows, summarize(rows))
            source = (Path(directory) / 'results-summary.typ').read_text()
            self.assertIn('[20% target FER], [—], [—], [—], [0 / 1]', source)

    def test_report_metadata_tracks_plan_and_saved_counts(self):
        rows = [dict(capacity_bps=c, requested_prop_us=d, payload_bytes=p, fer=f, ber=ber_for_fer(f, p + 10),
                     status='ok', transfer_s=5, data_s=4.5, mean_frame_bytes=p + 10,
                     actual_prop_us=d, file_bytes=4096, repetition=n)
                for c, d, p, f in configurations()
                for n in range(1, repetitions_for((c, d, p, f)) + 1)]
        with tempfile.TemporaryDirectory() as directory, patch('plot.ASSETS', Path(directory)):
            target = Path(directory) / 'results-meta.typ'
            plot.write_report_metadata(rows, summarize(rows))
            source = target.read_text()
            self.assertIn('total: 240', source)
            self.assertIn('current: true', source)
            plot.write_report_metadata(rows[:-1], summarize(rows[:-1]))
            source = target.read_text()
            self.assertIn('current: false', source)
            self.assertIn('planned_total: 240', source)

    def test_table_and_plots_receive_the_same_grouped_results(self):
        import csv
        rows = [dict(capacity_bps=c, requested_prop_us=d, payload_bytes=p, fer=f, ber=ber_for_fer(f, p + 10),
                     status='ok', transfer_s=5, data_s=4.5, mean_frame_bytes=p + 10,
                     actual_prop_us=d, file_bytes=4096, repetition=n,
                     max_payload=1000, input_file='sample.bin')
                for c, d, p, f in configurations()
                for n in range(1, repetitions_for((c, d, p, f)) + 1)]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with (folder / 'results.csv').open('w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch('plot.HERE', folder), patch('plot.ASSETS', folder / 'assets'), \
                    patch('sys.argv', ['plot.py']), patch('plot.plots') as draw, \
                    patch('plot.write_summary_table', wraps=plot.write_summary_table) as table, \
                    patch('plot.write_report_metadata', wraps=plot.write_report_metadata) as metadata:
                plot.main()
            self.assertIs(table.call_args.args[1], draw.call_args.args[0])
            self.assertIs(metadata.call_args.args[1], draw.call_args.args[0])
            source = (folder / 'assets/results-summary.typ').read_text()
            self.assertIn('[20% target FER], [5.00], [6.55], [68.3%], [20 / 20]', source)
            self.assertIn('240 of 240 transfers', source)

    def test_plot_profiles_and_delay_units(self):
        groups = [dict(capacity=c, delay=d, chunk_size=p, fer=f, efficiency=.7,
                       error=.01, theory=.8, propagation_ms=d / 1000, frame_bytes=p + 10)
                  for c, d, p, f in configurations()]
        with tempfile.TemporaryDirectory() as directory, \
                patch('plot.ASSETS', Path(directory)), \
                patch('plot.curve', wraps=plot.curve) as draw, \
                patch('matplotlib.figure.Figure.savefig') as save:
            plot.plots(groups)
        capacity, payload, delay, fer = draw.call_args_list
        self.assertEqual([len(call.args[1]) for call in draw.call_args_list], [6, 5, 5, 6])
        for call in (capacity, payload):
            self.assertTrue(all(g['fer'] == 0 and g['delay'] == 0 for g in call.args[1]))
        self.assertEqual([payload.args[2](g) for g in payload.args[1]], [64, 128, 256, 512, 1000])
        self.assertEqual([delay.args[2](g) for g in delay.args[1]], [0, 10, 25, 50, 100])
        self.assertTrue(all(g['delay'] == 0 for g in fer.args[1]))
        self.assertEqual(save.call_count, 4)


if __name__ == '__main__':
    unittest.main()
