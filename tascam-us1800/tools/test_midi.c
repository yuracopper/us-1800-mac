#include <CoreFoundation/CoreFoundation.h>
#include <CoreMIDI/CoreMIDI.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <stdio.h>

static MIDIClientRef g_midi_client;
static MIDIEndpointRef g_midi_source;
static MIDIEndpointRef g_midi_dest;

static void midi_read_proc(const MIDIPacketList *pktlist, void *refCon, void *connRefCon) {
    const MIDIPacket *packet = &pktlist->packet[0];
    for (UInt32 i = 0; i < pktlist->numPackets; i++) {
        printf("MIDI TX from CoreAudio to device: len=%d bytes=[", packet->length);
        for (int b = 0; b < packet->length; b++) printf("%02x ", packet->data[b]);
        printf("]\n");
        packet = MIDIPacketNext(packet);
    }
}

int main() {
    OSStatus st = MIDIClientCreate(CFSTR("US1800_Driver"), NULL, NULL, &g_midi_client);
    printf("MIDIClientCreate: %d\n", (int)st);

    st = MIDISourceCreate(g_midi_client, CFSTR("TASCAM US-1800 MIDI IN"), &g_midi_source);
    printf("MIDISourceCreate: %d\n", (int)st);

    st = MIDIDestinationCreate(g_midi_client, CFSTR("TASCAM US-1800 MIDI OUT"), midi_read_proc, NULL, &g_midi_dest);
    printf("MIDIDestinationCreate: %d\n", (int)st);

    printf("Virtual MIDI endpoints created successfully!\n");
    printf("Checking MIDI sources count: %ld, destinations count: %ld\n",
           MIDIGetNumberOfSources(), MIDIGetNumberOfDestinations());

    MIDIEndpointDispose(g_midi_source);
    MIDIEndpointDispose(g_midi_dest);
    MIDIClientDispose(g_midi_client);
    return 0;
}
