# Reliable File Transfer over a Serial Link

Computer Networks lab project at FEUP (2026/2027), by Rodrigo Fernandes Teixeira and Nuno João Pires Carvalho.

## Problem

Transfer a file between two Linux computers over an RS-232 serial link, preserving its contents despite transmission errors or temporary disconnections. The protocol must establish and close the connection, detect corrupted frames, recover through retransmission, and avoid delivering duplicate data.

## Our solution

We implemented two independent layers in C:

- **Application layer:** sends START and END packets containing file metadata, splits file contents into DATA packets, and reconstructs the received file. It validates packet lengths, the advertised file size, and matching START/END metadata.
- **Link layer:** transports application packets as opaque payloads in information frames (I frames). It implements framing, byte stuffing, XOR checksums, connection handshakes, and Stop-and-Wait ARQ with alternating sequence numbers. Replies (RR / REJ) acknowledge accepted frames or request retransmission; timers handle missing acknowledgements, and duplicate frames are acknowledged without delivering their contents twice.

The supplied serial-port implementation handles device access. The supplied virtual cable allows both endpoints to run on one machine and supports simulated noise, propagation delay, and disconnections. The application and link implementations are in `src/application_layer.c` and `src/link_layer.c`; the entry point, serial-port code, and cable were provided by the teaching staff.

The link accepts application packets of up to 1,000 bytes, leaving up to 997 file bytes per DATA packet. The executable uses a four-second timeout and a maximum of three retransmissions.

## Folder structure

```text
.
├── src/                 # Application/link implementations, serial access and entry point
├── cable/               # Supplied virtual serial cable
├── bin/                 # Compiled application and cable executables
├── report/              # Efficiency report PDF
├── Makefile             # Build, run, file comparison and cleanup targets
├── penguin.gif          # Sample file for transfers
└── README.txt           # Original starter instructions
```

## Build and run

### Requirements

Use Linux with GCC, GNU Make, and `socat` (required for the virtual cable). On Ubuntu or Debian:

```sh
sudo apt update
sudo apt install build-essential git socat
git clone https://github.com/teixeiraa05/rc_project.git
cd rc_project
make
```

Run all commands below from the repository root.

### Transfer the sample file locally

Use three terminals, keeping the virtual cable running throughout the transfer.

1. **Terminal 1 — start the cable:**

   ```sh
   make run_cable
   ```

   Wait for `Cable ready`. The cable creates `/tmp/ttyS10` for the transmitter and `/tmp/ttyS11` for the receiver.

2. **Terminal 2 — start the receiver:**

   ```sh
   make run_rx
   ```

3. **Terminal 3 — start the transmitter:**

   ```sh
   make run_tx
   ```

4. **After both endpoints finish, verify the received file:**

   ```sh
   diff -s penguin.gif penguin-received.gif
   ```

   Identical files confirm that the received contents match the original. The endpoints also print transfer progress and link statistics. Type `quit` in the cable terminal when finished.

### Transfer another file or use physical serial ports

The application accepts a serial device, baud rate, role, and file path:

```sh
./bin/main <serial-device> <baudrate> <tx|rx> <file-path>
```

For example, with the virtual cable already running, start the receiver before the transmitter in separate terminals:

```sh
# Receiver: destination file
./bin/main /tmp/ttyS11 9600 rx received.bin

# Transmitter: existing source file
./bin/main /tmp/ttyS10 9600 tx input.bin
```

Choose a destination path you can overwrite: reception replaces an existing file at that path, and a failed transfer can leave a partial file. Verify the received contents with `diff -s input.bin received.bin`.

For two machines connected by a serial cable, skip the virtual cable and use each machine's actual serial device, such as `/dev/ttyUSB0`. Both endpoints must use the same baud rate, and your user must have permission to access the devices. Supported baud rates are 1200, 1800, 2400, 4800, 9600, 19200, 38400, 57600, and 115200 bit/s.

### Simulate adverse conditions

In the virtual cable terminal, `help` lists the available controls. Use `off` / `on` to interrupt and restore the channel, `ber <value>` to inject bit errors (`ber 0` disables them), and `prop <microseconds>` to set propagation delay. BER is a bit error probability, distinct from the frame error rate studied in the report. Set baud rate and propagation delay before starting a transfer; changing them during transmission causes losses. A sufficiently long interruption or persistent noise can exhaust the retry limit.

`make clean` removes the executables and the default received file, `penguin-received.gif`.

## Experimental results

The efficiency study compares measured full-transfer efficiency with a theoretical Stop-and-Wait model while varying link capacity, I-frame size, propagation delay, and frame error rate. Across 380 transfers of a 16 KiB file, 376 delivered the file correctly. Larger frames improved efficiency by reducing protocol overhead; increasing capacity mainly improved throughput, while delay and errors reduced efficiency through waiting and retransmissions. The model closely followed error-free measurements, with larger differences under noise because it omits effects such as timeout waits.

See the [efficiency report](report/Rodrigo_Nuno_Lab1_Report.pdf) for the methodology, plots, numerical results, and conclusions.
