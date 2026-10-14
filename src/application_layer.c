// RCOM 2026/2027
//
// Application layer protocol implementation

#include "application_layer.h"
#include "link_layer.h"

#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define START 0x01
#define DATA 0x02
#define END 0x03

// Return the size in bytes, or -1 on error, resetting to the beginning.
static long getFileSize(FILE *file)
{
    if (fseek(file, 0, SEEK_END) != 0) return -1;

    long size = ftell(file);
    if (fseek(file, 0, SEEK_SET) != 0) return -1;

    return size;
}

// Return the packet length, or -1 on error.
static int buildControl(FILE *file, const char *filename, unsigned char control, unsigned char *packet)
{
    size_t nameLength = strlen(filename);
    if (nameLength > UINT8_MAX || 9 + nameLength > MAX_PAYLOAD_SIZE) {
        fprintf(stderr, "Filename is too long for the control packet.\n");
        return -1;
    }

    long size = getFileSize(file);
    if (size == -1) {
        perror("getFileSize");
        return -1;
    }
    if (size > UINT32_MAX) {
        fprintf(stderr, "File size exceeds the 4-byte size field.\n");
        return -1;
    }

    // Build START control packet
    packet[0] = control;
    packet[1] = 0; // File size parameter
    packet[2] = 4; // Its value is 4 bytes
    for (int i = 0; i < 4; i++) {
        packet[3 + i] = (size >> (8 * (3 - i))) & 0xFF;
    }

    packet[7] = 1; // File name parameter
    packet[8] = nameLength;
    memcpy(packet + 9, filename, nameLength);

    return 9 + nameLength;
}

static void sendFile(FILE *file, const char *filename)
{
    unsigned char packet[MAX_PAYLOAD_SIZE];
    int packetLength = buildControl(file, filename, START, packet);
    if (packetLength == -1) {
        fprintf(stderr, "Failed to build START control packet.\n");
        return;
    }

    // Send the START control packet
    if (llSend(packet, packetLength) == -1) {
        fprintf(stderr, "Failed to send START control packet.\n");
        return;
    }

    // Read and send the file data in DATA packets
    size_t bytesRead;
    while ((bytesRead = fread(packet + 3, 1, MAX_PAYLOAD_SIZE - 3, file)) > 0) {
        packet[0] = DATA;
        packet[1] = (bytesRead >> 8) & 0xFF; // High byte of length
        packet[2] = bytesRead & 0xFF;        // Low byte of length

        if (llSend(packet, bytesRead + 3) == -1) { // +3 for control and length bytes
            fprintf(stderr, "Failed to send DATA packet.\n");
            return;
        }
    }

    // Send the END control packet
    packetLength = buildControl(file, filename, END, packet);
    if (packetLength == -1) {
        fprintf(stderr, "Failed to build END control packet.\n");
        return;
    }
    if (llSend(packet, packetLength) == -1) {
        fprintf(stderr, "Failed to send END control packet.\n");
        return;
    }
}

void applicationLayer(const char *serialPort, const char *role, int baudRate,
                      int nTries, int timeout, const char *filename)
{

    FILE *file = fopen(filename, "rb");

    if (file == NULL) {
        perror("fopen");
        return;
    }

    
    // ----------------------------------------------------
    // TODO: Adapt and extend this code according to the specifications of the project.
    // ----------------------------------------------------

    LinkLayer llParameters = {
        .baudRate = baudRate,
        .nRetransmissions = nTries,
        .timeout = timeout,
    };
    strcpy(llParameters.serialPort, serialPort);

    if (strcmp(role, "tx") == 0)
    {
        llOpenTx(llParameters);
        sendFile(file, filename);
        llCloseTx();
    }
    else if (strcmp(role, "rx") == 0)
    {
        llOpenRx(llParameters);
        llCloseRx();
    }
    else
    {
        printf("Invalid role: %s. Must be 'tx' or 'rx'.\n", role);
        return;
    }
   
    fclose(file);
}
