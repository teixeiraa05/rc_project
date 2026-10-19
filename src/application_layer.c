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

// Return the packet type, or -1; START/END also supply the file size.
static int validatePacket(const unsigned char *packet, int packetLength, uint32_t *fileSize)
{
    if (packetLength < 1 || packetLength > MAX_PAYLOAD_SIZE) return -1;

    if (packet[0] == DATA) {
        if (packetLength < 4) return -1;
        int dataLength = packet[1] * 256 + packet[2];
        return dataLength == packetLength - 3 ? DATA : -1;
    }
    if (packet[0] != START && packet[0] != END) return -1;

    int hasSize = FALSE;
    int hasName = FALSE;
    uint32_t size = 0;
    for (int offset = 1; offset < packetLength;) {
        if (packetLength - offset < 2) return -1;
        unsigned char type = packet[offset++];
        int length = packet[offset++];
        if (length > packetLength - offset) return -1;

        if (type == 0) {
            if (hasSize || length != 4) return -1;
            hasSize = TRUE;
            for (int i = 0; i < length; i++)
                size = (size << 8) | packet[offset + i];
        } else if (type == 1) {
            if (hasName) return -1;
            hasName = TRUE;
        }
        offset += length;
    }

    if (!hasSize) return -1;
    *fileSize = size;
    return packet[0];
}

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

static int sendFile(FILE *file, const char *filename)
{
    unsigned char packet[MAX_PAYLOAD_SIZE];
    int packetLength = buildControl(file, filename, START, packet);
    if (packetLength == -1) {
        fprintf(stderr, "Failed to build START control packet.\n");
        return -1;
    }

    // Send the START control packet
    printf("[TX] Sending START packet (%d packet bytes).\n", packetLength);
    if (llSend(packet, packetLength) == -1) {
        fprintf(stderr, "Failed to send START control packet.\n");
        return -1;
    }
    printf("[TX] Full START packet sent and acknowledged.\n");

    // Read and send the file data in DATA packets
    size_t bytesRead;
    while ((bytesRead = fread(packet + 3, 1, MAX_PAYLOAD_SIZE - 3, file)) > 0) {
        packet[0] = DATA;
        packet[1] = (bytesRead >> 8) & 0xFF; // High byte of length
        packet[2] = bytesRead & 0xFF;        // Low byte of length

        printf("[TX] Sending DATA packet (%zu file bytes, %zu packet bytes).\n", bytesRead, bytesRead + 3);
        if (llSend(packet, bytesRead + 3) == -1) { // +3 for control and length bytes
            fprintf(stderr, "Failed to send DATA packet.\n");
            return -1;
        }
        printf("[TX] Full DATA packet sent and acknowledged (%zu file bytes).\n", bytesRead);
    }

    if (ferror(file)) {
        fprintf(stderr, "Failed to read input file.\n");
        return -1;
    }

    // Send the END control packet
    packetLength = buildControl(file, filename, END, packet);
    if (packetLength == -1) {
        fprintf(stderr, "Failed to build END control packet.\n");
        return -1;
    }
    printf("[TX] Sending END packet (%d packet bytes).\n", packetLength);
    if (llSend(packet, packetLength) == -1) {
        fprintf(stderr, "Failed to send END control packet.\n");
        return -1;
    }
    printf("[TX] Full END packet sent and acknowledged. File packets complete.\n");
    return 0;
}

static int receiveFile(const char *filename)
{
    unsigned char packet[MAX_PAYLOAD_SIZE];
    uint32_t fileSize;
    int packetLength = llReceive(packet);
    if (validatePacket(packet, packetLength, &fileSize) != START) {
        fprintf(stderr, "Failed to receive a valid START packet.\n");
        return -1;
    }
    printf("[RX] START packet received. Expecting %lu file bytes; saving to '%s'.\n",
        (unsigned long) fileSize, filename);

    unsigned char startPacket[MAX_PAYLOAD_SIZE];
    int startLength = packetLength;
    memcpy(startPacket, packet, startLength);

    FILE *file = fopen(filename, "wb");
    if (file == NULL) {
        perror("fopen");
        return -1;
    }

    uint32_t bytesWritten = 0;
    int complete = FALSE;
    int result = -1;
    while (TRUE) {
        uint32_t packetFileSize;
        packetLength = llReceive(packet);
        int type = validatePacket(packet, packetLength, &packetFileSize);
        if (type == DATA) {
            size_t dataLength = packetLength - 3;
            if (dataLength > fileSize - bytesWritten) {
                fprintf(stderr, "DATA exceeds the advertised file size.\n");
                break;
            }
            if (fwrite(packet + 3, 1, dataLength, file) != dataLength) {
                fprintf(stderr, "Failed to write output file.\n");
                break;
            }
            bytesWritten += dataLength;
            printf("[RX] DATA packet saved (%zu file bytes). Progress: %lu/%lu file bytes.\n",
                dataLength, (unsigned long) bytesWritten, (unsigned long) fileSize);
        } else if (type == END) {
            if (bytesWritten != fileSize || packetLength != startLength ||
                memcmp(packet + 1, startPacket + 1, startLength - 1) != 0) {
                fprintf(stderr, "END metadata or received file size does not match START.\n");
                break;
            }
            complete = TRUE;
            printf("[RX] END packet received. Metadata and file size match START.\n");
            result = 0;
            break;
        } else {
            fprintf(stderr, "Expected a valid DATA or END packet.\n");
            break;
        }
    }

    if (fclose(file) != 0) {
        perror("fclose");
        result = -1;
    }
    // shortcut: early errors cannot abort the link locally; add a link abort API for recovery.
    if (complete && llCloseRx() < 0) {
        fprintf(stderr, "Failed to close receiver connection.\n");
        result = -1;
    }
    if (result == 0)
        printf("[RX] File reception complete: '%s' (%lu bytes). Connection closed.\n",
            filename, (unsigned long) bytesWritten);
    return result;
}

void applicationLayer(const char *serialPort, const char *role, int baudRate,
                      int nTries, int timeout, const char *filename)
{

    LinkLayer llParameters = {
        .baudRate = baudRate,
        .nRetransmissions = nTries,
        .timeout = timeout,
    };
    strcpy(llParameters.serialPort, serialPort);

    if (strcmp(role, "tx") == 0)
    {
        FILE *file = fopen(filename, "rb");
        if (file == NULL) {
            perror("fopen");
            return;
        }
        if (llOpenTx(llParameters) < 0) {
            fprintf(stderr, "Failed to open transmitter connection.\n");
            fclose(file);
            return;
        }
        if (sendFile(file, filename) < 0)
            fprintf(stderr, "File transfer failed.\n");
        llCloseTx();
        if (fclose(file) != 0) perror("fclose");
    }
    else if (strcmp(role, "rx") == 0)
    {
        if (llOpenRx(llParameters) < 0) {
            fprintf(stderr, "Failed to open receiver connection.\n");
            return;
        }
        if (receiveFile(filename) < 0)
            fprintf(stderr, "File reception failed.\n");
    }
    else
    {
        printf("Invalid role: %s. Must be 'tx' or 'rx'.\n", role);
        return;
    }
}
