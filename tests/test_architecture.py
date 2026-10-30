"""Regression checks at the existing application and link interfaces."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ArchitectureChecks(unittest.TestCase):
    def compile_and_run(self, source, *cases):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / 'check.c'
            harness.write_text(source.replace('SOURCE_ROOT', str(ROOT / 'src')))
            subprocess.run(['gcc', '-Wall', str(harness), '-o', str(folder / 'check')],
                           check=True, capture_output=True, text=True)
            for case in cases:
                with self.subTest(case=case):
                    result = subprocess.run([folder / 'check', str(case)],
                                            capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_control_packets_reflect_current_file_size(self):
        self.compile_and_run(r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include "SOURCE_ROOT/application_layer.c"
static int delivered, mutation, endPackets;
static FILE *inputFile;
static unsigned char start[MAX_PAYLOAD_SIZE];
static int startLength;
int llOpenTx(LinkLayer p) { return 0; }
int llOpenRx(LinkLayer p) { return 0; }
int llCloseTx(void) { return 0; }
int llCloseRx(void) { return 0; }
int llReceive(unsigned char *p) { return -1; }
int llSend(const unsigned char *p, int length) {
    assert(length <= MAX_PAYLOAD_SIZE);
    if (p[0] == START) {
        uint32_t size;
        assert(validatePacket(p, length, &size) == START);
        assert(size == 0 || size == 2051);
        memcpy(start, p, length); startLength = length;
    } else if (p[0] == DATA) {
        assert(validatePacket(p, length, NULL) == DATA);
        for (int i = FRAME_HEADER_SIZE; i < length; i++)
            assert(p[i] == (unsigned char)delivered++);
        if (mutation && delivered == 2051)
            assert(ftruncate(fileno(inputFile), delivered + mutation) == 0);
    } else {
        endPackets++;
        assert(p[0] == END && length == startLength);
        uint32_t finalSize;
        assert(validatePacket(p, length, &finalSize) == END);
        assert(finalSize == (uint32_t)(delivered + mutation));
        assert((memcmp(p + 1, start + 1, length - 1) != 0) == (mutation != 0));
    }
    return length;
}
int main(int argc, char **argv) {
    int size = atoi(argv[1]);
    mutation = strstr(argv[1], "grow") ? 1 : strstr(argv[1], "shrink") ? -1 : 0;
    FILE *file = tmpfile(); assert(file);
    inputFile = file;
    for (int i = 0; i < size; i++) assert(fputc(i % 256, file) != EOF);
    rewind(file);
    assert(sendFile(file, "sample.bin") == 0);
    assert(endPackets == 1);
    assert(delivered == size);
    assert(fclose(file) == 0);
}
''', 0, 2051, '2051-grow', '2051-shrink')

    def test_link_handshakes_and_retry_limits(self):
        self.compile_and_run(r'''
#include <assert.h>
#include <stdlib.h>
#define alarm testAlarm
#include "SOURCE_ROOT/link_layer.c"
#undef alarm

static int mode, input[8192], count, position, writes, rrCount, closes, timers;
static unsigned int lastAlarm;
static void byte(int b) { assert(count < 8192); input[count++] = b; }
static void control(unsigned char a, unsigned char c) {
    byte(FLAG); byte(a); byte(c); byte(a ^ c); byte(FLAG);
}
static void info(unsigned char c, int corrupt) {
    byte(FLAG); byte(A_TX); byte(c); byte(A_TX ^ c);
    // Stuffed payload is opaque to the link and includes FLAG and ESC.
    byte(ESC); byte(ESCAPED_FLAG); byte(ESC); byte(ESCAPED_ESCAPED);
    byte((FLAG ^ ESC) ^ corrupt); byte(FLAG);
}
unsigned int testAlarm(unsigned int seconds) {
    lastAlarm = seconds;
    if (seconds) timers++;
    return 0;
}
int openSerialPort(const char *port, int baud) { return 1; }
int closeSerialPort(void) { closes++; return 0; }
int readByteSerialPort(unsigned char *b) {
    if (position == count) { errno = EIO; return -1; }
    int event = input[position++];
    if (event == -2) { alarmHandler(SIGALRM); errno = EINTR; return -1; }
    if (event == -1) { errno = EIO; return -1; }
    *b = event; return 1;
}
int writeBytesSerialPort(const unsigned char *p, int n) {
    writes++;
    if (n == SUP_FRAME_SIZE && (p[2] == RR0 || p[2] == RR1)) {
        rrCount++;
        assert(p[2] == RR1);
    }
    if (n == SUP_FRAME_SIZE && p[1] == A_RX && p[2] == DISC)
        control(A_RX, UA);
    return n;
}
int main(int argc, char **argv) {
    mode = atoi(argv[1]); savedRetries = 3; savedTimeout = 4;
    LinkLayer params = {.baudRate=9600, .timeout=4, .nRetransmissions=3};
    if (mode <= 1) {
        rxSequence = 1;
        if (mode == 1) byte(-2); // DISC can be delayed or lost; replies are reliable.
        control(A_TX, DISC);
        assert(llCloseRx() == 0);
        assert(rrCount == 0 && rxSequence == 1 && closes == 1);
        assert(timers == (mode == 1 ? 3 : 2));
    } else if (mode == 2) {
        for (int i=0; i<4; i++) byte(-2);
        assert(llOpenTx(params) == -1);
        assert(writes == 4 && closes == 1 && timers == 4);
    } else if (mode == 3) {
        byte(-2); control(A_TX, UA);
        assert(llOpenTx(params) == 0);
        assert(writes == 2 && timers == 2 && closes == 0);
    } else if (mode == 4) {
        control(A_TX, REJ0); control(A_TX, RR1);
        unsigned char payload[] = {FLAG, ESC};
        assert(llSend(payload, 2) == 2);
        assert(writes == 2 && txSequence == 1);
    } else if (mode == 5) {
        byte(-1);
        assert(llOpenTx(params) == -1);
        assert(writes == 1 && closes == 1 && timers == 1);
    } else if (mode == 6) {
        byte(-2); control(A_TX, SET);
        assert(llOpenRx(params) == 0);
        assert(writes == 1 && timers == 2 && rxSequence == 0);
    } else if (mode == 7) {
        control(A_TX, DISC); control(A_TX, RR1); control(A_TX, UA);
        assert(llOpenTx(params) == 0);
        assert(writes == 1 && timers == 1);
    } else if (mode == 8) {
        for (int i=0; i<4; i++) byte(-2);
        unsigned char payload[] = {FLAG, ESC};
        assert(llSend(payload, 2) == -1);
        assert(writes == 4 && txSequence == 0 && timers == 4);
    } else if (mode == 9) {
        for (int i=0; i<4; i++) byte(-2);
        assert(llCloseRx() == -1);
        assert(writes == 0 && timers == 4 && closes == 1);
    } else if (mode == 10) {
        info(I0, 0);
        unsigned char payload[MAX_PAYLOAD_SIZE];
        assert(llReceive(payload) == 2);
        assert(payload[0] == FLAG && payload[1] == ESC);
        assert(rxSequence == 1 && rrCount == 1);
        control(A_TX, DISC);
        assert(llCloseRx() == 0);
        assert(rxSequence == 1 && rrCount == 1);
    } else if (mode == 11) {
        rxSequence = 1;
        control(A_TX, SET); byte(-2); byte(-2); byte(-2); byte(-2);
        // An unrelated control frame must not restart the active timeout.
        assert(llCloseRx() == -1);
        assert(rrCount == 0 && timers == 4 && closes == 1);
    } else if (mode == 12) {
        byte(-1);
        assert(llCloseRx() == -1);
        assert(writes == 0 && closes == 1 && timers == 1);
    } else if (mode == 13) {
        rxSequence = 1; control(A_RX, DISC); control(A_TX, DISC);
        assert(llCloseRx() == 0);
        assert(rrCount == 0 && rxSequence == 1);
    } else if (mode == 14) {
        rxSequence = 1; control(A_TX, DISC); input[3] ^= 1;
        control(A_TX, DISC);
        assert(llCloseRx() == 0);
        assert(rrCount == 0 && rxSequence == 1);
    } else if (mode == 15) {
        rxSequence = 0; control(A_TX, DISC);
        assert(llCloseRx() == 0);
        assert(rrCount == 0 && rxSequence == 0);
    } else if (mode == 16) {
        control(A_TX, RR0); control(A_TX, REJ1); control(A_TX, RR1);
        unsigned char payload[] = {FLAG, ESC};
        assert(llSend(payload, 2) == 2);
        assert(writes == 1 && timers == 1 && txSequence == 1);
    } else if (mode == 17) {
        control(A_TX, DISC);
        for (int i=0; i<4; i++) byte(-2);
        assert(llOpenTx(params) == -1);
        assert(writes == 4 && timers == 4 && closes == 1);
    }
    assert(lastAlarm == 0);
}
''', *range(18))


if __name__ == '__main__':
    unittest.main()
