// RCOM 2026/2027
//
// Link layer protocol implementation

#include "link_layer.h"
#include "serial_port.h"

#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <string.h>

// MISC
#define _POSIX_SOURCE 1
#define SUP_FRAME_SIZE 5

#define TRUE 1
#define FALSE 0

#define FLAG 0x7E

#define A_TX 0x03 //commands sent by Tx or replies sent by Rx
#define A_RX 0x01 //commands sent by Rx or replies sent by Tx

#define SET 0x03
#define UA 0x07
#define RR0 0xAA
#define RR1 0xAB
#define REJ0 0x54
#define REJ1 0x55
#define DISC 0x0B
#define I0 0x00
#define I1 0x80
#define NO_ALT_CTRL 0x00

#define ESC 0x7D
#define ESCAPED_FLAG (FLAG ^ 0x20)
#define ESCAPED_ESC (ESC ^ 0x20)


static volatile sig_atomic_t alarmEnabled = FALSE;
static volatile sig_atomic_t alarmCount = 0;

static int expectedRxSequence = 0;
static int txSequence = 0;
static int savedTimeout = 0;
static int maxRetransmissions = 0;

typedef enum {
    START,
    FLAG_RCV,
    A_RCV,
    C_RCV,
    BCC_OK,
    BCC2_OK,
    DATA_RCV,
    DISCARD_DATA,
    STOP
} State;

// Values that must survive between calls to the information-frame parser.
typedef struct {
    State state;
    unsigned char control;
    unsigned char data[MAX_PAYLOAD_SIZE + 1];
    int dataLength;
    unsigned char bcc2; // XOR of destuffed payload bytes and the received BCC2.
    int valid;
    int escaped;
} IFrameParser;

typedef struct {
    State state;
    unsigned char control;
} SupervisionFrameParser;

static void alarmHandler(int signal) {
    alarmEnabled = FALSE;
    alarmCount++;
}

// Building the frame to send (supervision frames: SET, UA, RR, REJ, DISC)
static void buildSupervisionFrame(
    unsigned char address,
    unsigned char control,
    unsigned char frame[SUP_FRAME_SIZE])
{
    frame[0] = FLAG;
    frame[1] = address;
    frame[2] = control;
    frame[3] = address ^ control;
    frame[4] = FLAG;
}

// Write all 5 bytes
static int writeSupervisionFrame(unsigned char address, unsigned char control) {
    unsigned char frame[SUP_FRAME_SIZE];
    buildSupervisionFrame(address, control, frame);

    int bytes = writeBytesSerialPort(frame, SUP_FRAME_SIZE);
    if (bytes != SUP_FRAME_SIZE) {
        if (bytes < 0) perror("writeBytesSerialPort");
        else fprintf(stderr, "Incomplete frame written\n");
        return -1;
    }

    printf("%d bytes written to serial port\n", bytes);
    printf("FRAME = 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X\n",
        frame[0], frame[1], frame[2], frame[3], frame[4]);

    return 0;
}

// Parse a complete supervision frame using the expected address.
static void parseSupervisionFrameByte(unsigned char byte, unsigned char address, SupervisionFrameParser *parser) {
    switch (parser->state)
    {
        case START:
            if (byte == FLAG) parser->state = FLAG_RCV;
            break;
        case FLAG_RCV:
            if (byte == address) parser->state = A_RCV;
            else if (byte == FLAG) parser->state = FLAG_RCV;
            else parser->state = START;
            break;
        case A_RCV:
            if (byte == SET || byte == UA || byte == DISC ||
                byte == RR0 || byte == RR1 || byte == REJ0 || byte == REJ1) {
                parser->control = byte;
                parser->state = C_RCV;
            }
            else if (byte == FLAG) parser->state = FLAG_RCV;
            else parser->state = START;
            break;
        case C_RCV:
            if (byte == (address ^ parser->control)) parser->state = BCC_OK;
            else if (byte == FLAG) parser->state = FLAG_RCV;
            else parser->state = START;
            break;
        case BCC_OK:
            if (byte == FLAG) parser->state = STOP;
            else parser->state = START;
            break;
        default:
            break;
    }
}


// Byte stuffing
static int stuffByte(unsigned char byte, unsigned char *output) {
    if (byte == FLAG) {
        output[0] = ESC;
        output[1] = ESCAPED_FLAG;
        return 2;
    } else if (byte == ESC) {
        output[0] = ESC;
        output[1] = ESCAPED_ESC;
        return 2;
    }

    output[0] = byte;
    return 1;
}

//Byte stuffing. Returns 1 for a decoded byte, 0 for ESC, or -1 for an invalid escape.
static int destuffByte(unsigned char byte, unsigned char *output, int *escaped) {
    if (*escaped)
    {
        *escaped = 0;
        if (byte != ESCAPED_FLAG && byte != ESCAPED_ESC)
            return -1;
        *output = byte ^ 0x20;
        return 1;
    }

    if (byte == ESC)
    {
        *escaped = 1;
        return 0;
    }

    *output = byte;
    return 1;
}
/*
  Process one information-frame body byte: destuff and store data, accumulate
  its XOR checksum, and validate BCC2 when the closing flag arrives.
  Update the parser state to finish the frame or discard a malformed body.
*/
static void parseIFrameDataByte(unsigned char byte, IFrameParser *parser) {
    unsigned char decoded;
    
    // An unescaped flag ends the body; payload XOR received BCC2 must be zero.
    if (byte == FLAG && !parser->escaped) {
        parser->valid = (parser->dataLength >= 1 && parser->bcc2 == 0);
        // On success, remove the received checksum byte from the payload length.
        if (parser->valid)
            parser->dataLength--; // Exclude BCC2 from the payload length.
        // Otherwise, report a missing checksum or a nonzero body XOR.
        else
            fprintf(stderr, "[ERROR] Invalid BCC2\n");
        parser->state = STOP;
        return;
    }

    int result = destuffByte(byte, &decoded, &parser->escaped);

    // ESC produces no decoded byte yet; wait for the next input byte.
    if (result == 0) return; // Wait for the byte following ESC.

    // Reject an invalid escape and drain the body unless this byte ends it.
    if (result < 0) {
        parser->valid = FALSE;
        fprintf(stderr, "[ERROR] Invalid escape sequence\n");
        // A raw flag already ends the damaged frame.
        parser->state = byte == FLAG ? STOP : DISCARD_DATA;
        return;
    }

    // Reserve storage for at most MAX_PAYLOAD_SIZE data bytes plus one BCC2.
    if (parser->dataLength >= (int) sizeof(parser->data)) {
        parser->valid = FALSE;
        fprintf(stderr, "[ERROR] Oversized frame\n");
        parser->state = DISCARD_DATA;
        return;
    }

    // Store each decoded byte, including BCC2, and accumulate the body XOR.
    parser->data[parser->dataLength++] = decoded;
    parser->bcc2 ^= decoded;
}

// Parses information frames.
static void parseIFrameByte(unsigned char byte, IFrameParser *parser) {
    State *state = &parser->state;

    switch (*state)
    {
        case START:
            if (byte == FLAG) *state = FLAG_RCV;
            break;
        case FLAG_RCV:
            if (byte == A_TX) *state = A_RCV;
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case A_RCV:
            if (byte == I0 || byte == I1) {
                parser->control = byte;
                *state = C_RCV;
            }
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case C_RCV:
            if (byte == (A_TX ^ parser->control)) {
                parser->valid = FALSE;
                parser->dataLength = 0;
                parser->bcc2 = 0;
                parser->escaped = FALSE;
                *state = DATA_RCV;
            }
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case DATA_RCV: 
            parseIFrameDataByte(byte, parser);
            break; 
        case DISCARD_DATA:
            // Drain the damaged body before asking for retransmission.
            if (byte == FLAG) *state = STOP;
            break;
        case STOP:
            break;
        default:
            parser->valid = FALSE;
            *state = START;
            break;
    }
}

static int startAlarm(void) {
    struct sigaction act = {0};
    act.sa_handler = &alarmHandler;
    if (sigaction(SIGALRM, &act, NULL) == -1) {
        perror("sigaction");
        return -1;
    }
    alarmEnabled = TRUE;
    alarm(savedTimeout);
    return 0;
}

static void deactivateAlarm(void) {
    alarmEnabled = FALSE;
    alarm(0);
}

static int waitForSupervisionReply(unsigned char address, unsigned char expectedControl,
                                   unsigned char alternativeControl) {
    SupervisionFrameParser parser = {.state = START};
    if (startAlarm() < 0) return -1;

    while (parser.state != STOP && alarmEnabled) {
        unsigned char byte;
        int bytes = readByteSerialPort(&byte);
        if (bytes < 0) {
            if (errno == EINTR) continue;
            perror("readByteSerialPort");
            deactivateAlarm();
            return -1;
        }
        if (bytes == 0) continue;
        parseSupervisionFrameByte(byte, address, &parser);
        if (parser.state == STOP && parser.control != expectedControl && parser.control != alternativeControl)
            parser.state = START;
    }
    deactivateAlarm();
    return parser.state == STOP ? parser.control : -2;
}

// Active exchanges resend a control frame after each timeout.
static int exchangeSupervisionFrames(unsigned char sendAddress, unsigned char control,
                                     unsigned char replyAddress, unsigned char expectedControl) {
    for (int att = 0; att <= maxRetransmissions; att++) {
        if (writeSupervisionFrame(sendAddress, control) < 0) return -1;
        int ret = waitForSupervisionReply(replyAddress, expectedControl, NO_ALT_CTRL);
        if (ret == expectedControl || ret == -1) return ret;
    }
    return -2;
}

////////////////////////////////////////////////
// LLOPEN
////////////////////////////////////////////////
int llOpenTx(LinkLayer llParameters) {

    if (openSerialPort(llParameters.serialPort, llParameters.baudRate) < 0)
    {
        perror("openSerialPort");
        return -1;
    }

    savedTimeout = llParameters.timeout;
    maxRetransmissions = llParameters.nRetransmissions;
    txSequence = 0;

    printf("Serial port %s opened\n", llParameters.serialPort);

    if (exchangeSupervisionFrames(A_TX, SET, A_TX, UA) == UA) return 0;

    if (closeSerialPort() < 0)
        perror("closeSerialPort");
    return -1;
}

int llOpenRx(LinkLayer llParameters)
{
    if (openSerialPort(llParameters.serialPort, llParameters.baudRate) < 0)
    {
        perror("openSerialPort");
        return -1;
    }

    savedTimeout = llParameters.timeout;
    maxRetransmissions = llParameters.nRetransmissions;

    printf("Serial port %s opened\n", llParameters.serialPort);

    int ret = -2;
    for (int att = 0; att <= maxRetransmissions && ret == -2; att++)
        ret = waitForSupervisionReply(A_TX, SET, NO_ALT_CTRL);
    if (ret != SET) {
        closeSerialPort();
        return -1;
    }

    if (writeSupervisionFrame(A_TX, UA) < 0) {
        closeSerialPort();
        return -1;
    }

    printf("SET received; UA sent\n");
    expectedRxSequence = 0;

    return 0;
}

////////////////////////////////////////////////
// LLSEND
////////////////////////////////////////////////
int llSend(const unsigned char *buf, int bufSize)
{
    if (bufSize < 0 || bufSize > MAX_PAYLOAD_SIZE) return -1;

    /* 
        Build the frame once; retransmissions reuse it.
        
        2 * (MAX_PAYLOAD + 1) for the possible stuffing on every byte (and the BCC2, which might need stuffing)
        + 5 for the FLAG, address, control, BCC, FLAG 
    */
    unsigned char frame[2 * (MAX_PAYLOAD_SIZE + 1) + 5];
    int n = 0;
    unsigned char control = (txSequence == 0) ? I0 : I1;
    unsigned char bcc2 = 0;

    frame[n++] = FLAG;
    frame[n++] = A_TX;
    frame[n++] = control;
    frame[n++] = A_TX ^ control;

    for (int i = 0; i < bufSize; i++) {
        bcc2 ^= buf[i];
        n += stuffByte(buf[i], &frame[n]);
    }
    n += stuffByte(bcc2, &frame[n]);   
    frame[n++] = FLAG;

    unsigned char okResponse = (txSequence == 0) ? RR1 : RR0;
    unsigned char rejectResponse = (txSequence == 0) ? REJ0 : REJ1;

    for (int att = 0; att <= maxRetransmissions; att++) {
        int written = 0;
        while (written < n) {
            int bytes = writeBytesSerialPort(frame + written, n - written);
            if (bytes < 0) {
                if (errno == EINTR) continue;
                perror("writeBytesSerialPort");
                return -1;
            }
            if (bytes == 0) {
                fprintf(stderr, "Serial write made no progress\n");
                return -1;
            }
            written += bytes;
        }

        int ret = waitForSupervisionReply(A_TX, okResponse, rejectResponse);
        if (ret == -1) return -1;

        if (ret == okResponse) {            // acknowledged
            txSequence ^= 1;
            return bufSize;
        }
        // Timeout or matching REJ: retransmit.
    }

    return -1;                              // too many retransmissions
}

////////////////////////////////////////////////
// LLRECEIVE
////////////////////////////////////////////////
int llReceive(unsigned char *packet) {
    while (TRUE) {
        IFrameParser parser = {.state = START};

        while (parser.state != STOP) {
            unsigned char byte;
            int bytes = readByteSerialPort(&byte);

            if (bytes < 0) {
                if (errno == EINTR) continue;
                perror("readByteSerialPort");
                return -1;
            }
            if (bytes == 0) continue;
            parseIFrameByte(byte, &parser);
        }

        int receivedSequence = parser.control == I1;
        int isNew = receivedSequence == expectedRxSequence;

        if (!parser.valid && isNew) {
            unsigned char rej = expectedRxSequence == 0 ? REJ0 : REJ1;
            if (writeSupervisionFrame(A_TX, rej) < 0) return -1;
            continue;
        }

        int nextExpected = isNew ? (expectedRxSequence ^ 1) : expectedRxSequence;
        unsigned char rr = nextExpected == 0 ? RR0 : RR1;
        if (writeSupervisionFrame(A_TX, rr) < 0) return -1;

        // Acknowledge duplicates without delivering the payload twice.
        if (!isNew) continue;

        expectedRxSequence = nextExpected;
        memcpy(packet, parser.data, parser.dataLength);
        return parser.dataLength;
    }
}

////////////////////////////////////////////////
// LLCLOSE
////////////////////////////////////////////////
int llCloseTx()
{
    int ret = exchangeSupervisionFrames(A_TX, DISC, A_RX, DISC);
    if (ret != DISC) {
        if (closeSerialPort() < 0) perror("closeSerialPort");
        return -1;
    }

    if (writeSupervisionFrame(A_RX, UA) < 0) {
        closeSerialPort();
        return -1;
    }
    if (closeSerialPort() < 0) {
        perror("closeSerialPort");
        return -1;
    }
    return 0;
}

int llCloseRx()
{
    int ret = -2;
    for (int att = 0; att <= maxRetransmissions && ret == -2; att++)
        ret = waitForSupervisionReply(A_TX, DISC, NO_ALT_CTRL);
    if (ret != DISC) {
        closeSerialPort();
        return -1;
    }

    ret = exchangeSupervisionFrames(A_RX, DISC, A_RX, UA);
    if (ret != UA) {
        if (closeSerialPort() < 0) perror("closeSerialPort");
        return -1;
    }
    if (closeSerialPort() < 0) {
        perror("closeSerialPort");
        return -1;
    }
    return 0;
}
