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

#define RT_H2D_CLASS_EP   (USB_DIR_OUT | USB_TYPE_CLASS | USB_RECIP_ENDPOINT)   // 0x22
#define RT_D2H_CLASS_EP   (USB_DIR_IN  | USB_TYPE_CLASS | USB_RECIP_ENDPOINT)   // 0xa2
#define RT_H2D_VENDOR_DEV (USB_DIR_OUT | USB_TYPE_VENDOR | USB_RECIP_DEVICE)    // 0x40
#define RT_D2H_VENDOR_DEV (USB_DIR_IN  | USB_TYPE_VENDOR | USB_RECIP_DEVICE)    // 0xc0

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

    // Open interface 0 and 1
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

    if (!if0 || !if1) {
        printf("Failed to find interfaces\n");
        return 1;
    }

    (*if0)->USBInterfaceOpen(if0);
    (*if1)->USBInterfaceOpen(if1);

    // 1. Query firmware
    uint8_t fw_buf[16] = {0};
    kr = ctrl_msg(dev, VENDOR_REQ_FIRMWARE_READ, RT_D2H_VENDOR_DEV, 0, 0, fw_buf, 15);
    printf("Firmware query: kr=0x%x, str='%s'\n", kr, fw_buf + 4);

    // 2. Set alt settings
    kr = (*if0)->SetAlternateInterface(if0, 1);
    printf("if0 SetAlt 1: 0x%x\n", kr);
    kr = (*if1)->SetAlternateInterface(if1, 1);
    printf("if1 SetAlt 1: 0x%x\n", kr);

    // 3. Boot state read
    uint8_t boot_state = 0;
    kr = ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, &boot_state, 1);
    printf("Boot state handshake: kr=0x%x, val=0x%02x\n", kr, boot_state);

    // 4. Configure for rate 48000
    int rate = 48000;
    uint8_t payload[3] = { 0x80, 0xbb, 0x00 };
    uint16_t rate_reg = REG_ADDR_RATE_48000;

    kr = ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_CONFIG, 0x0002, NULL, 0);
    printf("MODE_VAL_CONFIG: kr=0x%x\n", kr);

    kr = ctrl_msg(dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, payload, 3);
    printf("UAC_SET_CUR EP_AUDIO_IN: kr=0x%x\n", kr);

    kr = ctrl_msg(dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_OUT, payload, 3);
    printf("UAC_SET_CUR EP_AUDIO_OUT: kr=0x%x\n", kr);

    uint8_t scratch[16] = {0};
    kr = ctrl_msg(dev, VENDOR_REQ_REGISTER_WRITE, RT_D2H_VENDOR_DEV, 0x0d00, REG_VAL_ENABLE, scratch, 5);
    printf("REG_WRITE 0x0d00: kr=0x%x, bytes=%02x %02x %02x %02x %02x\n",
           kr, scratch[0], scratch[1], scratch[2], scratch[3], scratch[4]);

    uint16_t regs[] = { REG_ADDR_INIT_0D, REG_ADDR_INIT_0E, REG_ADDR_INIT_0F, rate_reg, REG_ADDR_INIT_11 };
    for (int i = 0; i < 5; i++) {
        kr = ctrl_msg(dev, VENDOR_REQ_REGISTER_WRITE, RT_H2D_VENDOR_DEV, regs[i], REG_VAL_ENABLE, NULL, 0);
        printf("REG_WRITE 0x%04x: kr=0x%x\n", regs[i], kr);
    }

    memset(scratch, 0, sizeof(scratch));
    kr = ctrl_msg(dev, UAC_GET_CUR, RT_D2H_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, scratch, 3);
    printf("UAC_GET_CUR EP_AUDIO_IN: kr=0x%x, rate_bytes=%02x %02x %02x\n",
           kr, scratch[0], scratch[1], scratch[2]);

    kr = ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, scratch, 1);
    printf("HANDSHAKE_READ: kr=0x%x, val=0x%02x\n", kr, scratch[0]);

    kr = ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_START_US1800, 0, NULL, 0);
    printf("STREAM_START_US1800: kr=0x%x\n", kr);

    printf("Waiting 500ms in stream state...\n");
    usleep(500000);

    kr = ctrl_msg(dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);
    printf("STREAM_STOP_US1800: kr=0x%x\n", kr);

    (*if0)->USBInterfaceClose(if0);
    (*if0)->Release(if0);
    (*if1)->USBInterfaceClose(if1);
    (*if1)->Release(if1);
    (*dev)->USBDeviceClose(dev);
    (*dev)->Release(dev);

    printf("Done test hardware init successfully!\n");
    return 0;
}
