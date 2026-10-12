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

#define ESC 0x7D
#define ESCAPED_FLAG (FLAG ^ 0x20)
#define ESCAPED_ESCAPED (ESC ^ 0x20)


static int alarmEnabled = FALSE;
static int alarmCount = 0;

static int rxSequence = 0;
static int txSequence = 0;
static int savedTimeout = 0;
static int savedRetries = 0;

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
    // XOR of all destuffed body bytes, including the received BCC2.
    unsigned char bcc2;
    int valid;
    int escaped;
} FrameParserState;

typedef enum {
    TRANSMITTER,
    RECEIVER
} Role;

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

// For now just pass the role (used for SET/UA during llopen)
static void supervisionFrameParser(unsigned char byte, State *state, unsigned char address, unsigned char control) {
    unsigned char expectedBCC = address ^ control;

    switch (*state)
    {
        case START:
            if (byte == FLAG) *state = FLAG_RCV;
            break;
        case FLAG_RCV:
            if (byte == address) *state = A_RCV;
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case A_RCV:
            if (byte == control) *state = C_RCV;
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case C_RCV:
            if (byte == expectedBCC) *state = BCC_OK;
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case BCC_OK:
            if (byte == FLAG) *state = STOP;
            else *state = START;
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
        output[1] = ESCAPED_ESCAPED;
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
        if (byte != ESCAPED_FLAG && byte != ESCAPED_ESCAPED)
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

// Parses I-frames. Also recognises a retransmitted SET (lost UA), which has
// no body: it goes C_RCV -> BCC_OK -> STOP with valid = TRUE.
static void iFrameParser(unsigned char byte, FrameParserState *parser) {
    State *state = &parser->state;
    unsigned char decoded;

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
            if (byte == I0 || byte == I1 || byte == SET) {
                parser->control = byte;
                *state = C_RCV;
            }
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case C_RCV:
            if (byte == (A_TX ^ parser->control)) {
                parser->valid = FALSE;
                if (parser->control == SET) {
                    *state = BCC_OK;
                    break;
                }
                parser->dataLength = 0;
                parser->bcc2 = 0;
                parser->escaped = FALSE;
                *state = DATA_RCV;
            }
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;
        case BCC_OK:
            // Only reached for SET frames.
            if (byte == FLAG) {
                parser->valid = TRUE;
                *state = STOP;
            }
            else *state = START;
            break;
        case DATA_RCV: {
            if (byte == FLAG && !parser->escaped) {
                parser->valid = parser->dataLength >= 1 && parser->bcc2 == 0;
                if (parser->valid)
                    parser->dataLength--; // Exclude BCC2 from the payload length.
                else
                    fprintf(stderr, "[ERROR] Invalid BCC2\n");
                *state = STOP;
                break;
            }

            int result = destuffByte(byte, &decoded, &parser->escaped);
            if (result == 0) break; // Wait for the byte following ESC.
            if (result < 0) {
                parser->valid = FALSE;
                fprintf(stderr, "[ERROR] Invalid escape sequence\n");
                // A raw flag already ends the damaged frame.
                *state = byte == FLAG ? STOP : DISCARD_DATA;
                break;
            }

            if (parser->dataLength >= (int) sizeof(parser->data)) {
                parser->valid = FALSE;
                fprintf(stderr, "[ERROR] Oversized frame\n");
                *state = DISCARD_DATA;
                break;
            }

            parser->data[parser->dataLength++] = decoded;
            parser->bcc2 ^= decoded;
            break;
        }
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

typedef struct {
    State state;
    unsigned char control;   // which reply arrived: RR0, RR1, REJ0 or REJ1
} ResponseParser;

static void responseParser(unsigned char byte, ResponseParser *p) {
    switch (p->state) {
        case START:
            if (byte == FLAG) p->state = FLAG_RCV;
            break;
        case FLAG_RCV:
            if (byte == A_RX) p->state = A_RCV;
            else if (byte != FLAG) p->state = START;
            break;
        case A_RCV:
            if (byte == RR0 || byte == RR1 || byte == REJ0 || byte == REJ1) {
                p->control = byte;
                p->state = C_RCV;
            }
            else if (byte == FLAG) p->state = FLAG_RCV;
            else p->state = START;
            break;
        case C_RCV:
            if (byte == (A_RX ^ p->control)) p->state = BCC_OK;
            else if (byte == FLAG) p->state = FLAG_RCV;
            else p->state = START;
            break;
        case BCC_OK:
            p->state = (byte == FLAG) ? STOP : START;
            break;
        default:
            break;
    }
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
    savedRetries = llParameters.nRetransmissions;
    txSequence = 0;

    printf("Serial port %s opened\n", llParameters.serialPort);

    // Alarm installation
    struct sigaction act = {0};
    act.sa_handler = &alarmHandler;
    if (sigaction(SIGALRM, &act, NULL) == -1) {
        perror("sigaction");
        closeSerialPort();
        return -1;
    }

    for (int att = 0; att <= llParameters.nRetransmissions; att++) {

        State state = START;

        if (writeSupervisionFrame(A_TX, SET) < 0) {
            closeSerialPort();
            return -1;
        }

        alarmEnabled = TRUE;
        alarm(llParameters.timeout);

        while (state != STOP && alarmEnabled) {
            unsigned char byte;
            int bytes = readByteSerialPort(&byte);

            if (bytes < 0)
            {
                if (errno == EINTR) {
                    if (!alarmEnabled) break;
                    continue;
                }
                alarmEnabled = FALSE;
                alarm(0);
                perror("readByteSerialPort");
                closeSerialPort();
                return -1;
            }
            if (bytes == 0)
                continue;

            supervisionFrameParser(byte, &state, A_TX, UA);
        }

        alarmEnabled = FALSE;
        alarm(0);
        if (state == STOP)
            return 0;
    }

    // Close serial port on exceeding the limit of retransmissions
    if (closeSerialPort() < 0)
    {
        perror("closeSerialPort");
        return -1;
    }

    printf("Serial port %s closed\n", llParameters.serialPort);

    return -1;
}

int llOpenRx(LinkLayer llParameters)
{
    if (openSerialPort(llParameters.serialPort, llParameters.baudRate) < 0)
    {
        perror("openSerialPort");
        return -1;
    }

    printf("Serial port %s opened\n", llParameters.serialPort);

    // Wait for the SET supervision frame sent by the transmitter.
    State state = START;
    while (state != STOP)
    {
        unsigned char byte;
        int bytes = readByteSerialPort(&byte);
        if (bytes < 0)
        {
            perror("readByteSerialPort");
            closeSerialPort();
            return -1;
        }
        if (bytes == 0)
            continue;

        supervisionFrameParser(byte, &state, A_TX, SET);
    }

    if (writeSupervisionFrame(A_RX, UA) < 0) {
        closeSerialPort();
        return -1;
    }

    printf("SET received; UA sent\n");
    rxSequence = 0;

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

    for (int att = 0; att <= savedRetries; att++) {
        if (writeBytesSerialPort(frame, n) != n) return -1;

        ResponseParser p = {.state = START};
        alarmEnabled = TRUE;
        alarm(savedTimeout);

        while (p.state != STOP && alarmEnabled) {
            unsigned char byte;
            int bytes = readByteSerialPort(&byte);

            if (bytes < 0) {
                if (errno == EINTR) {
                    if (!alarmEnabled) break;
                    continue;
                }
                alarm(0);
                alarmEnabled = FALSE;
                perror("readByteSerialPort");
                return -1;
            }
            if (bytes == 0) continue;

            responseParser(byte, &p);
        }

        alarm(0);
        alarmEnabled = FALSE;

        if (p.state != STOP) continue;      // timeout: retransmit

        if (p.control == okResponse) {      // acknowledged
            txSequence ^= 1;
            return bufSize;
        }
        // REJ or RR for the same frame: loop and retransmit
    }

    return -1;                              // too many retransmissions
}

////////////////////////////////////////////////
// LLRECEIVE
////////////////////////////////////////////////
int llReceive(unsigned char *packet) {
    while (TRUE) {
        FrameParserState parser = {.state = START};

        while (parser.state != STOP) {
            unsigned char byte;
            int bytes = readByteSerialPort(&byte);

            if (bytes < 0) {
                if (errno == EINTR) continue;
                perror("readByteSerialPort");
                return -1;
            }
            if (bytes == 0) continue;
            iFrameParser(byte, &parser);
        }

        if (parser.control == SET) {
            if (writeSupervisionFrame(A_RX, UA) < 0) return -1;
            continue;
        }

        if (!parser.valid) {
            unsigned char rej = rxSequence == 0 ? REJ0 : REJ1;
            if (writeSupervisionFrame(A_RX, rej) < 0) return -1;
            continue;
        }

        int receivedSequence = parser.control == I1;
        int isNew = receivedSequence == rxSequence;
        int nextExpected = isNew ? (rxSequence ^ 1) : rxSequence;
        unsigned char rr = nextExpected == 0 ? RR0 : RR1;
        if (writeSupervisionFrame(A_RX, rr) < 0) return -1;

        // Acknowledge duplicates without delivering the payload twice.
        if (!isNew) continue;

        rxSequence = nextExpected;
        memcpy(packet, parser.data, parser.dataLength);
        return parser.dataLength;
    }
}

////////////////////////////////////////////////
// LLCLOSE
////////////////////////////////////////////////
int llCloseTx()
{
    // TODO: Implement this function

    return 0;
}

int llCloseRx()
{
    // TODO: Implement this function

    return 0;
}