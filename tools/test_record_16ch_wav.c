#include <IOKit/IOKitLib.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdint.h>

#define VID 0x0644
#define PID 0x8030

#define RT_H2D_CLASS_EP   0x22
#define RT_D2H_CLASS_EP   0xa2
#define RT_H2D_VENDOR_DEV 0x40
#define RT_D2H_VENDOR_DEV 0xc0

#define UAC_SET_CUR 0x01
#define UAC_GET_CUR 0x81
#define UAC_SAMPLING_FREQ_CONTROL 0x0100

#define VENDOR_REQ_POWER_CONTROL   0x00
#define VENDOR_REQ_REGISTER_WRITE  0x41
#define VENDOR_REQ_MODE_CONTROL    0x49
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

#define FRAMES_PER_PKT 6
#define ISOC_FRAMES 8
#define NUM_XFERS 8
#define PLAYBACK_CHANNELS 4
#define CAPTURE_CHANNELS 16
#define BYTES_PER_SAMPLE 3
#define PLAYBACK_FRAME_SIZE (PLAYBACK_CHANNELS * BYTES_PER_SAMPLE) // 12 bytes
#define CAPTURE_FRAME_SIZE  (CAPTURE_CHANNELS * BYTES_PER_SAMPLE)  // 48 bytes

static volatile int g_running = 1;
static IOUSBDeviceInterface300 **g_dev = NULL;
static IOUSBInterfaceInterface300 **g_if0 = NULL;
static IOUSBInterfaceInterface300 **g_if1 = NULL;
static int g_pipe_out = -1;
static int g_pipe_bulk_in = -1;
static UInt64 g_next_frame = 0;

typedef struct {
    uint8_t *audio;
    IOUSBLowLatencyIsocFrame *frames;
    UInt64 start_frame;
    int idx;
} OutXfer;

static OutXfer g_out_xfers[NUM_XFERS];

#define NUM_CAPTURE_BUFS 8
#define CAPTURE_BUF_SIZE 4096
static uint8_t g_capture_bufs[NUM_CAPTURE_BUFS][CAPTURE_BUF_SIZE];
static uint64_t g_captured_frames = 0;

static FILE *g_wav_file = NULL;
static uint32_t g_wav_data_bytes = 0;

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

// Decodes raw 64-byte frame into 16-channel S24_3LE (48 bytes per frame)
static void decode_frame_to_s24le(const uint8_t *src_even, const uint8_t *src_odd, uint8_t *dst)
{
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
        uint32_t val = ch[k]; // 24-bit value
        dst[k * 3 + 0] = (uint8_t)(val & 0xFF);
        dst[k * 3 + 1] = (uint8_t)((val >> 8) & 0xFF);
        dst[k * 3 + 2] = (uint8_t)((val >> 16) & 0xFF);
    }
}

static void write_wav_header(FILE *f, int channels, int rate, uint32_t data_size)
{
    fseek(f, 0, SEEK_SET);
    uint32_t riff_size = 36 + data_size;
    uint32_t byte_rate = rate * channels * 3;
    uint16_t block_align = channels * 3;
    uint16_t bits_per_sample = 24;

    fwrite("RIFF", 1, 4, f);
    fwrite(&riff_size, 4, 1, f);
    fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f);
    uint32_t fmt_chunk_size = 16;
    uint16_t format_tag = 1; // PCM
    uint16_t num_ch = channels;
    uint32_t sr = rate;
    fwrite(&fmt_chunk_size, 4, 1, f);
    fwrite(&format_tag, 2, 1, f);
    fwrite(&num_ch, 2, 1, f);
    fwrite(&sr, 4, 1, f);
    fwrite(&byte_rate, 4, 1, f);
    fwrite(&block_align, 2, 1, f);
    fwrite(&bits_per_sample, 2, 1, f);
    fwrite("data", 1, 4, f);
    fwrite(&data_size, 4, 1, f);
}

static void resync_out(void)
{
    UInt64 f = 0;
    AbsoluteTime t;
    (*g_if0)->GetBusFrameNumber(g_if0, &f, &t);
    g_next_frame = f + 50;
}

static void submit_out(OutXfer *x);

static void on_out_complete(void *ref, IOReturn result, void *arg0)
{
    (void)arg0;
    OutXfer *x = (OutXfer *)ref;
    if (result != kIOReturnSuccess) resync_out();
    if (g_running) submit_out(x);
}

static void submit_out(OutXfer *x)
{
    int pkt_bytes = FRAMES_PER_PKT * PLAYBACK_FRAME_SIZE;
    int total_bytes = ISOC_FRAMES * pkt_bytes;
    memset(x->audio, 0, total_bytes);

    for (int i = 0; i < ISOC_FRAMES; i++) {
        x->frames[i].frStatus = 0;
        x->frames[i].frReqCount = pkt_bytes;
        x->frames[i].frActCount = 0;
        x->frames[i].frTimeStamp.hi = 0;
        x->frames[i].frTimeStamp.lo = 0;
    }

    x->start_frame = g_next_frame;
    g_next_frame += 1;

    kern_return_t kr = (*g_if0)->LowLatencyWriteIsochPipeAsync(
        g_if0, g_pipe_out, x->audio, x->start_frame,
        ISOC_FRAMES, 1, x->frames, on_out_complete, x);

    if (kr == (kern_return_t)0xe00002ee) {
        resync_out();
        x->start_frame = g_next_frame;
        g_next_frame += 1;
        (*g_if0)->LowLatencyWriteIsochPipeAsync(
            g_if0, g_pipe_out, x->audio, x->start_frame,
            ISOC_FRAMES, 1, x->frames, on_out_complete, x);
    }
}

static void queue_bulk_read(int buf_idx);

static void on_bulk_in_complete(void *refCon, IOReturn result, void *arg0)
{
    int buf_idx = (int)(intptr_t)refCon;
    UInt32 bytes_transferred = (UInt32)(uintptr_t)arg0;

    if (result == kIOReturnSuccess && bytes_transferred >= 64 && g_wav_file) {
        int frames = bytes_transferred / 64;
        uint8_t s24_buf[64 * CAPTURE_FRAME_SIZE];

        for (int f = 0; f < frames; f++) {
            const uint8_t *src_even = g_capture_bufs[buf_idx] + (f * 64);
            const uint8_t *src_odd  = g_capture_bufs[buf_idx] + (f * 64) + 32;
            decode_frame_to_s24le(src_even, src_odd, s24_buf + (f * CAPTURE_FRAME_SIZE));
        }

        size_t written = fwrite(s24_buf, 1, frames * CAPTURE_FRAME_SIZE, g_wav_file);
        g_wav_data_bytes += (uint32_t)written;
        g_captured_frames += frames;
    }

    if (g_running) {
        queue_bulk_read(buf_idx);
    }
}

static void queue_bulk_read(int buf_idx)
{
    (*g_if1)->ReadPipeAsync(
        g_if1, g_pipe_bulk_in, g_capture_bufs[buf_idx], CAPTURE_BUF_SIZE,
        on_bulk_in_complete, (void *)(intptr_t)buf_idx);
}

int main(int argc, char *argv[]) {
    const char *out_wav_path = "test_recording_16ch.wav";
    g_wav_file = fopen(out_wav_path, "wb");
    if (!g_wav_file) {
        printf("Failed to create %s\n", out_wav_path);
        return 1;
    }
    // Write placeholder header
    write_wav_header(g_wav_file, CAPTURE_CHANNELS, 48000, 0);

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

    UInt8 n0, n1;
    (*g_if0)->GetNumEndpoints(g_if0, &n0);
    for (UInt8 p = 1; p <= n0; p++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*g_if0)->GetPipeProperties(g_if0, p, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBOut && tt == kUSBIsoc) g_pipe_out = p;
    }

    (*g_if1)->GetNumEndpoints(g_if1, &n1);
    for (UInt8 p = 1; p <= n1; p++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*g_if1)->GetPipeProperties(g_if1, p, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBIn && tt == kUSBBulk && num == 6) g_pipe_bulk_in = p;
    }

    CFRunLoopSourceRef src0 = NULL, src1 = NULL;
    (*g_if0)->CreateInterfaceAsyncEventSource(g_if0, &src0);
    (*g_if1)->CreateInterfaceAsyncEventSource(g_if1, &src1);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src0, kCFRunLoopDefaultMode);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), src1, kCFRunLoopDefaultMode);

    int max_pkt = PLAYBACK_CHANNELS * BYTES_PER_SAMPLE * FRAMES_PER_PKT;
    UInt32 abuf_sz = ISOC_FRAMES * max_pkt;
    UInt32 fbuf_sz = ISOC_FRAMES * sizeof(IOUSBLowLatencyIsocFrame);

    for (int i = 0; i < NUM_XFERS; i++) {
        void *a = NULL, *f = NULL;
        (*g_if0)->LowLatencyCreateBuffer(g_if0, &a, abuf_sz, kUSBLowLatencyWriteBuffer);
        (*g_if0)->LowLatencyCreateBuffer(g_if0, &f, fbuf_sz, kUSBLowLatencyFrameListBuffer);
        g_out_xfers[i].audio = (uint8_t *)a;
        g_out_xfers[i].frames = (IOUSBLowLatencyIsocFrame *)f;
        g_out_xfers[i].idx = i;
    }

    resync_out();
    for (int i = 0; i < NUM_XFERS; i++) submit_out(&g_out_xfers[i]);

    for (int i = 0; i < NUM_CAPTURE_BUFS; i++) {
        queue_bulk_read(i);
    }

    printf("Recording 16 channels to %s for 3 seconds...\n", out_wav_path);
    time_t start = time(NULL);
    while (g_running && difftime(time(NULL), start) < 3.0) {
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.01, false);
    }
    g_running = 0;

    for (int i = 0; i < 5; i++)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false);

    // Finalize WAV header
    write_wav_header(g_wav_file, CAPTURE_CHANNELS, 48000, g_wav_data_bytes);
    fclose(g_wav_file);
    g_wav_file = NULL;

    printf("Finished recording! Total frames: %llu, total bytes: %u\n",
           (unsigned long long)g_captured_frames, g_wav_data_bytes);

    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);

    CFRunLoopRemoveSource(CFRunLoopGetCurrent(), src0, kCFRunLoopDefaultMode);
    CFRunLoopRemoveSource(CFRunLoopGetCurrent(), src1, kCFRunLoopDefaultMode);
    CFRelease(src0);
    CFRelease(src1);

    for (int i = 0; i < NUM_XFERS; i++) {
        if (g_out_xfers[i].audio) (*g_if0)->LowLatencyDestroyBuffer(g_if0, g_out_xfers[i].audio);
        if (g_out_xfers[i].frames) (*g_if0)->LowLatencyDestroyBuffer(g_if0, g_out_xfers[i].frames);
    }

    (*g_if0)->USBInterfaceClose(g_if0);
    (*g_if0)->Release(g_if0);
    (*g_if1)->USBInterfaceClose(g_if1);
    (*g_if1)->Release(g_if1);
    (*g_dev)->USBDeviceClose(g_dev);
    (*g_dev)->Release(g_dev);
    return 0;
}
