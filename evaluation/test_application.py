"""Verify chunking against the application's actual DATA packet builder."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from run import ROOT


class ApplicationChunkChecks(unittest.TestCase):
    def test_serial_port_length_boundary(self):
        source = r'''
#include <assert.h>
#include "APPLICATION_SOURCE"

static int opens;
int llOpenRx(LinkLayer parameters) {
    assert(strlen(parameters.serialPort) == 49);
    opens++;
    return -1;
}
int llOpenTx(LinkLayer parameters) { assert(0); return -1; }
int llSend(const unsigned char *packet, int length) { return -1; }
int llReceive(unsigned char *packet) { return -1; }
int llCloseTx(void) { return 0; }
int llCloseRx(void) { return 0; }
int main(void) {
    char port[101];
    memset(port, 'x', sizeof(port));
    port[49] = '\0';
    applicationLayer(port, "rx", 9600, 3, 4, "unused.bin");
    assert(opens == 1);
    port[49] = 'x';
    port[50] = '\0';
    applicationLayer(port, "rx", 9600, 3, 4, "unused.bin");
    assert(opens == 1);
    port[50] = 'x';
    port[100] = '\0';
    applicationLayer(port, "rx", 9600, 3, 4, "unused.bin");
    assert(opens == 1);
}
'''.replace('APPLICATION_SOURCE', str(ROOT / 'src/application_layer.c'))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / 'port_check.c'
            harness.write_text(source)
            executable = folder / 'check'
            subprocess.run(['gcc', '-Wall', str(harness), '-o', str(executable)],
                           check=True, capture_output=True)
            result = subprocess.run([executable], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr.count('Serial port name is too long.'), 2)

    def test_receiver_connection_cleanup(self):
        source = r'''
#include <assert.h>
#include <stdlib.h>
#include "APPLICATION_SOURCE"

static int scenario, reads, closes;
int llOpenTx(LinkLayer parameters) { return 0; }
int llOpenRx(LinkLayer parameters) { return scenario == 0 ? -1 : 0; }
int llSend(const unsigned char *packet, int length) { return length; }
int llCloseTx(void) { return 0; }
int llCloseRx(void) {
    closes++;
    puts("TEST_CLOSE");
    return scenario == 5 ? -1 : 0;
}
int llReceive(unsigned char *packet) {
    int first = reads++ == 0;
    if (scenario == 1 || (!first && scenario == 3)) return -1;
    unsigned char control[] = {first ? START : END, 0, 4, 0, 0, 0, 0};
    if (!first && scenario == 6) control[6] = 1;
    memcpy(packet, control, sizeof(control));
    return sizeof(control);
}
int main(int argc, char **argv) {
    assert(argc == 3);
    scenario = atoi(argv[1]);
    applicationLayer("/tmp/test-port", "rx", 9600, 3, 4, argv[2]);
    assert(closes == (scenario == 0 ? 0 : 1));
}
'''.replace('APPLICATION_SOURCE', str(ROOT / 'src/application_layer.c'))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / 'cleanup_check.c'
            harness.write_text(source)
            executable = folder / 'check'
            subprocess.run(['gcc', '-Wall', str(harness), '-o', str(executable)],
                           check=True, capture_output=True)
            for scenario in range(7):
                with self.subTest(scenario=scenario):
                    destination = folder if scenario == 2 else folder / 'output.bin'
                    result = subprocess.run([executable, str(scenario), str(destination)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if scenario == 4:
                        self.assertIn('File reception complete:', result.stdout)
                        self.assertLess(result.stdout.index('TEST_CLOSE'),
                                        result.stdout.index('File reception complete:'))
                    else:
                        self.assertNotIn('File reception complete:', result.stdout)
                    if scenario == 5:
                        self.assertIn('Failed to close receiver connection.', result.stderr)

    def test_chunk_boundaries_and_fixed_link_limit(self):
        source = r'''
#include <assert.h>
#include "APPLICATION_SOURCE"

static int delivered, dataPackets;
int llSend(const unsigned char *packet, int length) {
    assert(length <= MAX_PAYLOAD_SIZE);
    if (packet[0] == DATA) {
        int expected = 2051 - delivered;
        if (expected > FILE_CHUNK_SIZE) expected = FILE_CHUNK_SIZE;
        assert(length == expected + FRAME_HEADER_SIZE);
        assert(packet[1] * 256 + packet[2] == expected);
        for (int i = 0; i < expected; i++)
            assert(packet[FRAME_HEADER_SIZE + i] == (unsigned char)(delivered + i));
        delivered += expected;
        dataPackets++;
    }
    return length;
}
int llOpenTx(LinkLayer parameters) { return 0; }
int llOpenRx(LinkLayer parameters) { return 0; }
int llReceive(unsigned char *packet) { return -1; }
int llCloseTx(void) { return 0; }
int llCloseRx(void) { return 0; }
int main(void) {
    assert(MAX_PAYLOAD_SIZE == 1000);
    FILE *file = tmpfile();
    assert(file);
    for (int i = 0; i < 2051; i++) assert(fputc(i % 256, file) != EOF);
    rewind(file);
    assert(sendFile(file, "sample.bin") == 0);
    assert(delivered == 2051);
    assert(dataPackets == (2051 + FILE_CHUNK_SIZE - 1) / FILE_CHUNK_SIZE);
    assert(fclose(file) == 0);
}
'''.replace('APPLICATION_SOURCE', str(ROOT / 'src/application_layer.c'))
        original_header = (ROOT / 'src/link_layer.h').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            harness = folder / 'chunk_check.c'
            harness.write_text(source)
            for chunk in (None, 64, 512):
                with self.subTest(chunk=chunk or 'default (997)'):
                    command = ['gcc', '-Wall', str(harness), '-o', str(folder / 'check')]
                    if chunk is not None:
                        command.append(f'-DFILE_CHUNK_SIZE={chunk}')
                    subprocess.run(command, check=True, capture_output=True)
                    subprocess.run([folder / 'check'], check=True, capture_output=True)
            for invalid in (0, 1000):
                with self.subTest(invalid=invalid):
                    result = subprocess.run(['gcc', f'-DFILE_CHUNK_SIZE={invalid}',
                                             '-c', str(ROOT / 'src/application_layer.c'),
                                             '-o', str(folder / 'invalid.o')], capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'FILE_CHUNK_SIZE must fit', result.stderr)
        self.assertEqual((ROOT / 'src/link_layer.h').read_bytes(), original_header)


if __name__ == '__main__':
    unittest.main()
