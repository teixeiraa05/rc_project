# Run the evaluator

From the repository root:

```sh
python3 evaluation/run.py
python3 -m unittest discover -s evaluation -p test_evaluation.py
```

The evaluator uses 16 thread-pool workers with isolated cables and ports.
Every transfer sends the same 4096-byte file and checks byte-for-byte integrity.

## Profiles

| Sweep | Values | Fixed conditions |
| --- | --- | --- |
| Target FER | 0%, 2.5%, 5%, 10%, 15%, 20% | Capacity 9600 bit/s; Frame size L=512 bytes before stuffing (503 file bytes); no added propagation delay |
| One-way propagation delay | 0, 10, 25, 50, 100 ms | Capacity 9600 bit/s; Frame size L=512 bytes before stuffing (503 file bytes); FER 0% |
| Capacity | 1800, 2400, 4800, 9600, 19200, 38400 bit/s | Frame size L=512 bytes before stuffing (503 file bytes); FER 0%; no added propagation delay |
| Frame size L before stuffing | 64, 128, 256, 512, 1000 bytes | Capacity 9600 bit/s; FER 0%; no added propagation delay |

`L` is the complete frame size before stuffing, following the protocol slide 11
layout. Six link overhead bytes surround the application packet, so `P=L-6`;
its three-byte header leaves `P-3=L-9` file bytes. The frame-size sweep therefore
uses chunks of 55, 119, 247, 503 and 991 file bytes. The other profiles use
`L=512`, `P=506`, and 503 file bytes. `MAX_PAYLOAD_SIZE` remains 1000.
Actual stuffed frame lengths are recorded and used for timing and BER, rather
than assuming transmitted frames have exactly the nominal configured size.
In the CSV files, `payload_bytes` is the file chunk size (`P-3`), not the full
link payload `P`; `nominal_frame_bytes` is `L` before stuffing, while
`full_frame_bytes` and `mean_frame_bytes` are measured after stuffing. The latter
averages all DATA frames, including the shorter final frame. The existing
`packet_size` sweep key and CSV filename refer to the frame-size profile.

There are **19 unique configurations, each run 20 times: 380 transfers**.
Shared configurations are evaluated once and reused across plots and the summary
table. The 512-byte frame-size setting is the shared reference for the other sweeps.

## Seeds and noise

Each CSV row records a 32-bit seed derived with SHA-256 from base seed 20261104,
its configuration and repetition. A private cable copy uses independently seeded
`rand_r` streams for each direction, reset before each transfer. The teacher's
cable source is unchanged. On the same C runtime, seeds reproduce the sequence of
fault decisions per direction; scheduling and timeout races can still change
transfer outcomes and elapsed times. Different repetitions use different seeds.

A rerun reuses complete seeded groups with matching configurations and captured
START/END lengths and the same nominal frame size; it runs missing groups only. Remove results.csv for a fresh run.

Clean trials provide full stuffed DATA-frame lengths for BER calibration.
The cable BER targets the configured FER of a full DATA frame; shorter DATA and
START/END frames have different error probabilities. Noise applies in both
directions. Endpoint baud stays at 9600; cable capacity sets simulated speed.
The protocol uses a four-second timeout and three retransmissions.

## Full-transfer prediction

Measured throughput is `R = 8M / T`, using full-transfer wall time from transmitter
launch until both endpoints finish. The theoretical efficiency is
`S = 8M / (C * E[Ttransfer])`, where predicted duration includes:

- Opening SET/UA: `100/C + 2*Tprop`.
- START, each DATA frame, and END: `(10*(L+5)/C + 2*Tprop)/(1-pe)` each; here `L` is the measured stuffed frame length, while configured profile sizes are nominal sizes before stuffing.
- Closing DISC/DISC/UA: `150/C + 3*Tprop`.

Recorded frame lengths include stuffing; DATA mean length includes the short
final frame. Separate START/END lengths are captured before their first attempt.
Predictions use actual cable propagation delay and no measured transfer times.

This is an ideal class-model reference: clean handshakes, independent errors,
equal-duration attempts and unlimited retries. It applies target FER uniformly
to information-frame attempts; processing, reply corruption, timeout waits,
retry-limit failures and process-launch/polling overhead are not modelled.
Statistics and standard errors use successful trials; failed trials remain in
CSV files and success counts.
