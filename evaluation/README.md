# Run the evaluator

From the repository root:

```sh
python3 evaluation/run.py
```

The evaluator runs all profiles with eight parallel workers. Every transfer sends
the same 4096-byte file.

## Profiles

| Sweep | Values | Fixed conditions |
| --- | --- | --- |
| Target FER | 0%, 2.5%, 5%, 10%, 15%, 20% | Capacity 9600 bit/s; file chunk 512 bytes; no added propagation delay |
| One-way propagation delay | 0, 10, 25, 50, 100 ms | Capacity 9600 bit/s; file chunk 512 bytes; FER 0% |
| Capacity | 1800, 2400, 4800, 9600, 19200, 38400 bit/s | File chunk 512 bytes; FER 0% and 5%; no added propagation delay |
| Application DATA packet size | 64, 128, 256, 512, 1000 bytes | Capacity 9600 bit/s; FER 0% and 5%; no added propagation delay |

Application DATA packet sizes include the three-byte header, so the packet-size
sweep uses file chunks of 61, 125, 253, 509 and 997 bytes. The other sweeps use
512 file bytes per DATA packet, plus the header. `MAX_PAYLOAD_SIZE` stays fixed
at 1000 bytes; link framing and byte stuffing add further wire overhead.

There are 30 unique configurations: the six FER-sweep configurations run 20 times
each, and the remaining 24 run five times each, giving **240 transfers**. Shared
configurations run once with their assigned repetition count.

Clean trials provide the mean full stuffed DATA-frame length for each payload
size. The evaluator calculates the cable BER from that length and the target FER
before running the corresponding error trials. Endpoint baud stays at 9600;
cable capacity controls simulated transmission speed. The protocol uses a
four-second timeout and three retransmissions.
