// RCOM 2026/2027
//
// Link layer protocol implementation

#include "link_layer.h"
#include "serial_port.h"

#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>

// MISC
#define _POSIX_SOURCE 1 // POSIX compliant source
#define SUP_FRAME_SIZE 5

#define TRUE 1
#define FALSE 0

#define FLAG 0x7E
#define A_TX 0x03
#define A_RX 0x01
#define SET 0x03
#define UA 0x07

static int alarmEnabled = FALSE;
static int alarmCount = 0; 

typedef enum {
    START,
    FLAG_RCV,
    A_RCV,
    C_RCV,
    BCC_OK,
    STOP
} State; 

typedef enum {
    TRANSMITTER,
    RECEIVER
} Role;

static void alarmHandler(int signal) {
    alarmEnabled = FALSE;
    alarmCount++;
}

//building the frame to send (SUPERVISION only for now, later DISC) 
static void buildSupervisionFrame (
    unsigned char address,
    unsigned char control, 
    unsigned char frame[SUP_FRAME_SIZE]
)  
{
    frame[0] = FLAG;
    frame[1] = address; 
    frame[2] = control; 
    frame[3] = address^control;
    frame[4] = FLAG;
}

//write all 5 bytes
static int writeSupervisionFrame (unsigned char address, unsigned char control) {
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


//for now just pass the role, but later, to include the DISC, we would need to pass the address and the control bytes specifically as arguments
static void frameParser(Role role, unsigned char byte, State *state) {

    unsigned char expectedA = (role == TRANSMITTER) ? A_RX : A_TX;
    unsigned char expectedC = (role == TRANSMITTER) ? UA : SET;
    unsigned char expectedBCC = (role == TRANSMITTER) ? (A_RX ^ UA) : (A_TX ^ SET);

    switch (*state)
    {
        case START:
            if (byte == FLAG) *state = FLAG_RCV;
            break; 
        case FLAG_RCV:
            if (byte == expectedA) *state = A_RCV;
            else if (byte == FLAG) *state = FLAG_RCV;
            else *state = START;
            break;   
        case A_RCV:
            if (byte == expectedC) *state = C_RCV;
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
        case STOP: 
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

    printf("Serial port %s opened\n", llParameters.serialPort);

    //alarm installation
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

            // printf("Tx received byte = 0x%02X\n", byte);
            frameParser(TRANSMITTER, byte, &state);
        }

        alarmEnabled = FALSE;
        alarm(0);
        if (state == STOP) {
            // if (closeSerialPort() < 0) {
            //     perror("closeSerialPort");
            //     return -1;
            // }

            // printf("Serial port %s closed\n", llParameters.serialPort);
            return 0;
        }
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

        // printf("Rx received byte = 0x%02X\n", byte);
        frameParser(RECEIVER, byte, &state);

    }    

    if(writeSupervisionFrame(A_RX, UA) < 0) {
        closeSerialPort();
        return -1; 
    }

    printf("SET received; UA sent\n");

    // Close serial port
    // if (closeSerialPort() < 0)
    // {
    //     perror("closeSerialPort");
    //     return -1;
    // }

    // printf("Serial port %s closed\n", llParameters.serialPort);

    return 0;
}

////////////////////////////////////////////////
// LLSEND
////////////////////////////////////////////////
int llSend(const unsigned char *buf, int bufSize)
{
    // TODO: Implement this function

    return 0;
}

////////////////////////////////////////////////
// LLRECEIVE
////////////////////////////////////////////////
int llReceive(unsigned char *packet)
{
    // TODO: Implement this function

    return 0;
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
