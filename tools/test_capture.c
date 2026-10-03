#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define VID 0x0644
#define PID 0x8030

#define USB_DIR_OUT         0
#define USB_DIR_IN          0x80
#define USB_TYPE_STANDARD   (0x00 << 5)
#define USB_TYPE_CLASS      (0x01 << 5)
#define USB_TYPE_VENDOR     (0x02 << 5)
#define USB_RECIP_DEVICE    0x00
#define USB_RECIP_INTERFACE 0x01
#define USB_RECIP_ENDPOINT  0x02

#define RT_H2D_CLASS_EP   (USB_DIR_OUT | USB_TYPE_CLASS | USB_RECIP_ENDPOINT)
#define RT_D2H_CLASS_EP   (USB_DIR_IN  | USB_TYPE_CLASS | USB_RECIP_ENDPOINT)
#define RT_H2D_VENDOR_DEV (USB_DIR_OUT | USB_TYPE_VENDOR | USB_RECIP_DEVICE)
#define RT_D2H_VENDOR_DEV (USB_DIR_IN  | USB_TYPE_VENDOR | USB_RECIP_DEVICE)

#define UAC_SET_CUR 0x01
#define UAC_GET_CUR 0x81
#define UAC_SAMPLING_FREQ_CONTROL 0x0100

#define VENDOR_REQ_POWER_CONTROL   0x00
#define VENDOR_REQ_REGISTER_WRITE  0x41
#define VENDOR_REQ_MODE_CONTROL    0x49
#define VENDOR_REQ_FIRMWARE_READ   0x56

#define MODE_VAL_HANDSHAKE_READ      0x0000
#define MODE_VAL_WAKE_UP             0x000d
#define MODE_VAL_CONFIG              0x0010
#define MODE_VAL_STREAM_START_US1800 0x0032
#define MODE_VAL_STREAM_STOP_US1800  0x0036
#define MODE_VAL_DEEP_SLEEP          0x0044

#define REG_ADDR_INIT_0D    0x0d04
#define REG_ADDR_INIT_0E    0x0e00
#define REG_ADDR_INIT_0F    0x0f00
#define REG_ADDR_RATE_44100 0x1000
#define REG_ADDR_RATE_48000 0x1002
#define REG_ADDR_RATE_88200 0x1008
#define REG_ADDR_RATE_96000 0x100a
#define REG_ADDR_INIT_11    0x110b

#define REG_VAL_ENABLE 0x0101

#define EP_PLAYBACK_FEEDBACK 0x81
#define EP_AUDIO_OUT         0x02
#define EP_MIDI_IN           0x83
#define EP_MIDI_OUT          0x04
#define EP_AUDIO_IN          0x86

static kern_return_t ctrl_msg(IOUSBDeviceInterface300 **dev,
                             uint8_t req, uint8_t rt, uint16_t val, uint16_t idx,
                             void *data, uint16_t len)
{
    IOUSBDevRequest r;
    memset(&r, 0, sizeof(r));
    r.bmRequestType = rt;
    r.bRequest = req;
    r.wValue = val;
    r.wIndex = idx;
    r.wLength = len;
    r.pData = data;
    return (*dev)->DeviceRequest(dev, &r);
}

static void us1800_decode_capture_chunk(const uint8_t *src, uint32_t *dst, int frames_to_decode)
{
    for (int f = 0; f < frames_to_decode; f++) {
        const uint8_t *src_even = src + (f * 64);
        const uint8_t *src_odd = src + (f * 64) + 32;
        uint32_t ch[16] = {0};

        for (int i = 0; i < 24; i++) {
            uint8_t v14 = src_even[i];
            ch[0]  = (ch[0] << 1)  | (v14 & 0x01);
            ch[2]  = (ch[2] << 1)  | ((v14 >> 1) & 0x01);
            ch[4]  = (ch[4] << 1)  | ((v14 >> 2) & 0x01);
            ch[6]  = (ch[6] << 1)  | ((v14 >> 3) & 0x01);
            ch[8]  = (ch[8] << 1)  | ((v14 >> 4) & 0x01);
            ch[10] = (ch[10] << 1) | ((v14 >> 5) & 0x01);
            ch[12] = (ch[12] << 1) | ((v14 >> 6) & 0x01);
            ch[14] = (ch[14] << 1) | ((v14 >> 7) & 0x01);
        }

        for (int i = 0; i < 24; i++) {
            uint8_t v24 = src_odd[i];
            ch[1]  = (ch[1] << 1)  | (v24 & 0x01);
            ch[3]  = (ch[3] << 1)  | ((v24 >> 1) & 0x01);
            ch[5]  = (ch[5] << 1)  | ((v24 >> 2) & 0x01);
            ch[7]  = (ch[7] << 1)  | ((v24 >> 3) & 0x01);
            ch[9]  = (ch[9] << 1)  | ((v24 >> 4) & 0x01);
            ch[11] = (ch[11] << 1) | ((v24 >> 5) & 0x01);
            ch[13] = (ch[13] << 1) | ((v24 >> 6) & 0x01);
            ch[15] = (ch[15] << 1) | ((v24 >> 7) & 0x01);
        }

        for (int k = 0; k < 16; k++) {
            dst[k] = ch[k] << 8;
        }

        dst += 16;
    }
}

int main(void) {
    CFMutableDictionaryRef match = IOServiceMatching(kIOUSBDeviceClassName);
    int32_t vid = VID, pid = PID;
    CFNumberRef vr = CFNumberCreate(NULL, kCFNumberSInt32Type, &vid);
    CFNumberRef pr = CFNumberCreate(NULL, kCFNumberSInt32Type, &pid);
    CFDictionarySetValue(match, CFSTR(kUSBVendorID), vr);
    CFDictionarySetValue(match, CFSTR(kUSBProductID), pr);
    CFRelease(vr); CFRelease(pr);

    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault, match);
    if (!svc) { printf("US-1800 not found\n"); return 1; }

    IOCFPlugInInterface **plug = NULL;
    SInt32 score;
    IOCreatePlugInInterfaceForService(svc, kIOUSBDeviceUserClientTypeID,
                                      kIOCFPlugInInterfaceID, &plug, &score);
    IOObjectRelease(svc);
    if (!plug) { printf("PlugIn error\n"); return 1; }

    IOUSBDeviceInterface300 **dev = NULL;
    (*plug)->QueryInterface(plug, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID300),
                            (LPVOID *)&dev);
    (*plug)->Release(plug);
    if (!dev) { printf("Dev error\n"); return 1; }

    kern_return_t kr = (*dev)->USBDeviceOpen(dev);
    if (kr != kIOReturnSuccess) {
        printf("USBDeviceOpen failed: 0x%08x\n", kr);
        return 1;
    }
    (*dev)->SetConfiguration(dev, 1);

    IOUSBFindInterfaceRequest req = {
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare,
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare
    };
    io_iterator_t iter;
    (*dev)->CreateInterfaceIterator(dev, &req, &iter);
    io_service_t isvc;
    IOUSBInterfaceInterface300 **if0 = NULL, **if1 = NULL;
    int idx = 0;
    while ((isvc = IOIteratorNext(iter))) {
        IOCFPlugInInterface **iplug = NULL;
        IOCreatePlugInInterfaceForService(isvc, kIOUSBInterfaceUserClientTypeID,
                                          kIOCFPlugInInterfaceID, &iplug, &score);
        IOObjectRelease(isvc);
        if (iplug) {
            IOUSBInterfaceInterface300 **iface = NULL;
            (*iplug)->QueryInterface(iplug, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID300), (LPVOID *)&iface);
            (*iplug)->Release(iplug);
            if (idx == 0) if0 = iface;
            else if (idx == 1) if1 = iface;
            else if (iface) { (*iface)->Release(iface); }
        }
        idx++;
    }
    IOObjectRelease(iter);

    if (!if0 || !if1) { printf("No ifaces\n"); return 1; }

    (*if0)->USBInterfaceOpen(if0);
    (*if1)->USBInterfaceOpen(if1);

    uint8_t fw_buf[16] = {0};
    ctrl_msg(dev, VENDOR_REQ_FIRMWARE_READ, RT_D2H_VENDOR_DEV, 0, 0, fw_buf, 15);
    (*if0)->SetAlternateInterface(if0, 1);
    (*if1)->SetAlternateInterface(if1, 1);

    uint8_t boot_state = 0;
    ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, &boot_state, 1);

    uint8_t payload[3] = { 0x80, 0xbb, 0x00 };
    ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_CONFIG, 0x0002, NULL, 0);
    ctrl_msg(dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, payload, 3);
    ctrl_msg(dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_OUT, payload, 3);

    uint8_t scratch[16] = {0};
    ctrl_msg(dev, VENDOR_REQ_REGISTER_WRITE, RT_D2H_VENDOR_DEV, 0x0d00, REG_VAL_ENABLE, scratch, 5);

    uint16_t regs[] = { REG_ADDR_INIT_0D, REG_ADDR_INIT_0E, REG_ADDR_INIT_0F, REG_ADDR_RATE_48000, REG_ADDR_INIT_11 };
    for (int i = 0; i < 5; i++) {
        ctrl_msg(dev, VENDOR_REQ_REGISTER_WRITE, RT_H2D_VENDOR_DEV, regs[i], REG_VAL_ENABLE, NULL, 0);
    }
    ctrl_msg(dev, UAC_GET_CUR, RT_D2H_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, scratch, 3);
    ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, scratch, 1);
    ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_START_US1800, 0, NULL, 0);

    printf("Stream started. Testing Bulk Read on EP 0x86 (pipe 2 of if1)...\n");

    // Find bulk in pipe on if1
    UInt8 numPipes = 0;
    (*if1)->GetNumEndpoints(if1, &numPipes);
    int bulk_pipe = -1;
    for (UInt8 p = 1; p <= numPipes; p++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*if1)->GetPipeProperties(if1, p, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBIn && tt == kUSBBulk && num == 6) {
            bulk_pipe = p;
            break;
        }
    }
    printf("Found bulk in pipe: %d\n", bulk_pipe);
    if (bulk_pipe < 0) {
        printf("Pipe not found\n");
        return 1;
    }

    // Try reading 4096 bytes with timeout
    uint8_t raw_buf[4096];
    uint32_t decoded[16 * 64]; // up to 64 frames
    for (int pkt = 0; pkt < 5; pkt++) {
        UInt32 size = sizeof(raw_buf);
        kr = (*if1)->ReadPipeTO(if1, bulk_pipe, raw_buf, &size, 1000, 1000);
        printf("Packet %d: ReadPipeTO kr=0x%x, size=%u\n", pkt, kr, size);
        if (kr == kIOReturnSuccess && size >= 64) {
            int frames = size / 64;
            us1800_decode_capture_chunk(raw_buf, decoded, frames);
            printf("Decoded %d frames (16ch). Sample 0: ch0=0x%08x, ch1=0x%08x, ch2=0x%08x, ch8=0x%08x\n",
                   frames, decoded[0], decoded[1], decoded[2], decoded[8]);
        }
    }

    ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);
    printf("Stream stopped.\n");

    (*if0)->USBInterfaceClose(if0);
    (*if0)->Release(if0);
    (*if1)->USBInterfaceClose(if1);
    (*if1)->Release(if1);
    (*dev)->USBDeviceClose(dev);
    (*dev)->Release(dev);
    return 0;
}
