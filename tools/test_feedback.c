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

#define RT_H2D_CLASS_EP   0x22
#define RT_D2H_CLASS_EP   0xa2
#define RT_H2D_VENDOR_DEV 0x40
#define RT_D2H_VENDOR_DEV 0xc0

#define UAC_SET_CUR 0x01
#define UAC_GET_CUR 0x81
#define UAC_SAMPLING_FREQ_CONTROL 0x0100

#define VENDOR_REQ_MODE_CONTROL    0x49
#define VENDOR_REQ_REGISTER_WRITE  0x41
#define VENDOR_REQ_FIRMWARE_READ   0x56

#define MODE_VAL_HANDSHAKE_READ      0x0000
#define MODE_VAL_CONFIG              0x0010
#define MODE_VAL_STREAM_START_US1800 0x0032
#define MODE_VAL_STREAM_STOP_US1800  0x0036

#define REG_ADDR_INIT_0D    0x0d04
#define REG_ADDR_INIT_0E    0x0e00
#define REG_ADDR_INIT_0F    0x0f00
#define REG_ADDR_RATE_48000 0x1002
#define REG_ADDR_INIT_11    0x110b
#define REG_VAL_ENABLE      0x0101

#define EP_AUDIO_OUT 0x02
#define EP_PLAYBACK_FEEDBACK 0x81

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

static volatile int g_running = 1;
static IOUSBDeviceInterface300 **g_dev = NULL;
static IOUSBInterfaceInterface300 **g_if0 = NULL;
static IOUSBInterfaceInterface300 **g_if1 = NULL;
static int g_pipe_out = -1;
static int g_pipe_fb = -1;

#define NUM_FB_XFERS 4
#define FB_PACKET_SIZE 64
typedef struct {
    uint8_t *buf;
    IOUSBLowLatencyIsocFrame *frames;
    UInt64 start_frame;
    int idx;
} FbXfer;

static FbXfer g_fb_xfers[NUM_FB_XFERS];
static UInt64 g_fb_next_frame = 0;
static int g_fb_received_count = 0;

static void resync_fb(void) {
    UInt64 f = 0;
    AbsoluteTime t;
    (*g_if1)->GetBusFrameNumber(g_if1, &f, &t);
    g_fb_next_frame = f + 20;
}

static void submit_fb(FbXfer *x);

static void on_fb_complete(void *refCon, IOReturn result, void *arg0) {
    (void)arg0;
    FbXfer *x = (FbXfer *)refCon;
    if (result == kIOReturnSuccess && x->frames) {
        for (int i = 0; i < 1; i++) {
            if (x->frames[i].frStatus == kIOReturnSuccess && x->frames[i].frActCount > 0) {
                g_fb_received_count++;
                uint8_t *p = x->buf + x->frames[i].frActCount;
                if (g_fb_received_count <= 10 || g_fb_received_count % 100 == 0) {
                    printf("[FB] xfer #%d: count=%u act=%u bytes=[%02x %02x %02x]\n",
                           g_fb_received_count, x->frames[i].frReqCount, x->frames[i].frActCount,
                           x->buf[0], x->buf[1], x->buf[2]);
                }
            }
        }
    } else {
        resync_fb();
    }
    if (g_running) submit_fb(x);
}

static void submit_fb(FbXfer *x) {
    x->frames[0].frStatus = 0;
    x->frames[0].frReqCount = 3;
    x->frames[0].frActCount = 0;
    x->frames[0].frTimeStamp.hi = 0;
    x->frames[0].frTimeStamp.lo = 0;

    x->start_frame = g_fb_next_frame;
    g_fb_next_frame += 4; // interval 4

    kern_return_t kr = (*g_if1)->LowLatencyReadIsochPipeAsync(
        g_if1, g_pipe_fb, x->buf, x->start_frame,
        1, 1, x->frames, on_fb_complete, x);

    if (kr == (kern_return_t)0xe00002ee) {
        resync_fb();
        x->start_frame = g_fb_next_frame;
        g_fb_next_frame += 4;
        (*g_if1)->LowLatencyReadIsochPipeAsync(
            g_if1, g_pipe_fb, x->buf, x->start_frame,
            1, 1, x->frames, on_fb_complete, x);
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
    if (!svc) return 1;

    IOCFPlugInInterface **plug = NULL;
    SInt32 score;
    IOCreatePlugInInterfaceForService(svc, kIOUSBDeviceUserClientTypeID,
                                      kIOCFPlugInInterfaceID, &plug, &score);
    IOObjectRelease(svc);

    (*plug)->QueryInterface(plug, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID300), (LPVOID *)&g_dev);
    (*plug)->Release(plug);

    (*g_dev)->USBDeviceOpen(g_dev);
    (*g_dev)->SetConfiguration(g_dev, 1);

    IOUSBFindInterfaceRequest req = {
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare,
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare
    };
    io_iterator_t iter;
    (*g_dev)->CreateInterfaceIterator(g_dev, &req, &iter);
    io_service_t isvc;
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
            if (idx == 0) g_if0 = iface;
            else if (idx == 1) g_if1 = iface;
            else if (iface) { (*iface)->Release(iface); }
        }
        idx++;
    }
    IOObjectRelease(iter);

    (*g_if0)->USBInterfaceOpen(g_if0);
    (*g_if1)->USBInterfaceOpen(g_if1);

    uint8_t fw_buf[16] = {0};
    ctrl_msg(g_dev, VENDOR_REQ_FIRMWARE_READ, RT_D2H_VENDOR_DEV, 0, 0, fw_buf, 15);
    (*g_if0)->SetAlternateInterface(g_if0, 1);
    (*g_if1)->SetAlternateInterface(g_if1, 1);

    uint8_t boot_state = 0;
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, &boot_state, 1);

    uint8_t payload[3] = { 0x80, 0xbb, 0x00 };
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_CONFIG, 0x0002, NULL, 0);
    ctrl_msg(g_dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, 0x86, payload, 3);
    ctrl_msg(g_dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, 0x02, payload, 3);

    uint8_t scratch[16] = {0};
    ctrl_msg(g_dev, VENDOR_REQ_REGISTER_WRITE, RT_D2H_VENDOR_DEV, 0x0d00, REG_VAL_ENABLE, scratch, 5);

    uint16_t regs[] = { REG_ADDR_INIT_0D, REG_ADDR_INIT_0E, REG_ADDR_INIT_0F, REG_ADDR_RATE_48000, REG_ADDR_INIT_11 };
    for (int i = 0; i < 5; i++) {
        ctrl_msg(g_dev, VENDOR_REQ_REGISTER_WRITE, RT_H2D_VENDOR_DEV, regs[i], REG_VAL_ENABLE, NULL, 0);
    }
    ctrl_msg(g_dev, UAC_GET_CUR, RT_D2H_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, 0x86, scratch, 3);
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, scratch, 1);
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_START_US1800, 0, NULL, 0);

    // Find feedback pipe (Pipe 1 on if1)
    UInt8 n1;
    (*g_if1)->GetNumEndpoints(g_if1, &n1);
    for (UInt8 p = 1; p <= n1; p++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*g_if1)->GetPipeProperties(g_if1, p, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBIn && tt == kUSBIsoc && num == 1) g_pipe_fb = p;
    }
    printf("Pipe feedback: %d\n", g_pipe_fb);

    CFRunLoopSourceRef src1 = NULL;
    (*g_if1)->CreateInterfaceAsyncEventSource(g_if1, &src1);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src1, kCFRunLoopDefaultMode);

    for (int i = 0; i < NUM_FB_XFERS; i++) {
        void *a = NULL, *f = NULL;
        (*g_if1)->LowLatencyCreateBuffer(g_if1, &a, 64, kUSBLowLatencyReadBuffer);
        (*g_if1)->LowLatencyCreateBuffer(g_if1, &f, sizeof(IOUSBLowLatencyIsocFrame), kUSBLowLatencyFrameListBuffer);
        g_fb_xfers[i].buf = (uint8_t *)a;
        g_fb_xfers[i].frames = (IOUSBLowLatencyIsocFrame *)f;
        g_fb_xfers[i].idx = i;
    }

    resync_fb();
    for (int i = 0; i < NUM_FB_XFERS; i++) submit_fb(&g_fb_xfers[i]);

    printf("Listening to feedback endpoint for 2 seconds...\n");
    time_t t0 = time(NULL);
    while (g_running && difftime(time(NULL), t0) < 2.0) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, false);
    }
    g_running = 0;

    printf("Done feedback test! Received feedback packets: %d\n", g_fb_received_count);

    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);

    CFRunLoopRemoveSource(CFRunLoopGetCurrent(), src1, kCFRunLoopDefaultMode);
    CFRelease(src1);

    for (int i = 0; i < NUM_FB_XFERS; i++) {
        if (g_fb_xfers[i].buf) (*g_if1)->LowLatencyDestroyBuffer(g_if1, g_fb_xfers[i].buf);
        if (g_fb_xfers[i].frames) (*g_if1)->LowLatencyDestroyBuffer(g_if1, g_fb_xfers[i].frames);
    }

    (*g_if0)->USBInterfaceClose(g_if0);
    (*g_if0)->Release(g_if0);
    (*g_if1)->USBInterfaceClose(g_if1);
    (*g_if1)->Release(g_if1);
    (*g_dev)->USBDeviceClose(g_dev);
    (*g_dev)->Release(g_dev);
    return 0;
}
