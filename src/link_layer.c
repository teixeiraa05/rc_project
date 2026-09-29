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
#define BUF_SIZE 5

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
} Machine;

static void alarmHandler(int signal) {
    alarmEnabled = FALSE;
    alarmCount++;
}

void frameStateHandler(Machine m, unsigned char byte, State *state) {

    unsigned char expectedA = (m == TRANSMITTER) ? A_RX : A_TX;
    unsigned char expectedC = (m == TRANSMITTER) ? UA : SET;
    unsigned char expectedBCC = (m == TRANSMITTER) ? (A_RX ^ UA) : (A_TX ^ SET);


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

    // Create string to send
    unsigned char set[BUF_SIZE] = {FLAG, A_TX, SET, A_TX ^ SET, FLAG};  
    
    for (int att = 0; att <= llParameters.nRetransmissions; att++) {

        State state = START;
        int nBytesBuf = 0;

        int bytes = writeBytesSerialPort(set, BUF_SIZE);
        if (bytes != BUF_SIZE) {
            if (bytes < 0) perror("writeBytesSerialPort");
            else fprintf(stderr, "Incomplete SET frame written\n");
            closeSerialPort();
            return -1;
        }
        // printf("Tx SET = 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X\n",
        //       set[0], set[1], set[2], set[3], set[4]);
        
        alarmEnabled = TRUE;
        alarm(llParameters.timeout); 

        printf("%d bytes written to serial port\n", bytes);
        
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
            frameStateHandler(TRANSMITTER, byte, &state);
            
            nBytesBuf += bytes;   
        }

        alarmEnabled = FALSE;
        alarm(0);
        if (state == STOP) {
            if (closeSerialPort() < 0) {
                perror("closeSerialPort");
                return -1;
            }

            printf("Serial port %s closed\n", llParameters.serialPort);
            return 0;
        }
    }
    
    // Close serial port
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
    // ----------------------------------------------------
    // This example code shows how to open the serial port and receive a string.
    // TODO: Adapt and extend this code according to the specifications of the project.
    // ----------------------------------------------------

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
        frameStateHandler(RECEIVER, byte, &state);

    }

    unsigned char ua[BUF_SIZE] = {FLAG, A_RX, UA, A_RX ^ UA, FLAG};
    // printf("Rx UA = 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X\n",
    //           ua[0], ua[1], ua[2], ua[3], ua[4]);

    int bytes = writeBytesSerialPort(ua, BUF_SIZE);
    if (bytes < 0)
    {
        perror("writeBytesSerialPort");
        closeSerialPort();
        return -1;
    }

    printf("SET received; UA sent\n");

    // Close serial port
    if (closeSerialPort() < 0)
    {
        perror("closeSerialPort");
        return -1;
    }

    printf("Serial port %s closed\n", llParameters.serialPort);

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
