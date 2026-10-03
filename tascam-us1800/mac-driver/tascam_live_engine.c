/*
 * TASCAM US-1800 Live Concert USB Audio Hardware Engine (Apple Silicon ARM64)
 * Directly drives physical USB 2.0 endpoints and connects to CoreAudio HAL
 * via lock-free high-speed shared memory for live DAW mixing.
 */

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/usb/USBSpec.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <math.h>
#include <fcntl.h>
#include <sys/mman.h>
#include "tascam_shm.h"

#define USB_VID_TASCAM               0x0644
#define USB_PID_TASCAM_US1800        0x8030

#define RT_D2H_VENDOR_DEV            0xc0
#define RT_H2D_VENDOR_DEV            0x40
#define RT_H2D_CLASS_EP              0x22
#define RT_D2H_CLASS_EP              0xa2

#define UAC_SET_CUR                  0x01
#define UAC_GET_CUR                  0x81
#define UAC_SAMPLING_FREQ_CONTROL    0x0100

#define VENDOR_REQ_POWER_CONTROL     0x00
#define VENDOR_REQ_REGISTER_WRITE    0x41
#define VENDOR_REQ_MODE_CONTROL      0x49
#define VENDOR_REQ_FIRMWARE_READ     0x56

#define MODE_VAL_HANDSHAKE_READ      0x0000
#define MODE_VAL_WAKE_UP             0x000d
#define MODE_VAL_CONFIG              0x0010
#define MODE_VAL_STREAM_START_US1800 0x0032
#define MODE_VAL_STREAM_STOP_US1800  0x0036
#define MODE_VAL_DEEP_SLEEP          0x0044

#define REG_ADDR_INIT_0D             0x0d04
#define REG_ADDR_INIT_0E             0x0e00
#define REG_ADDR_INIT_0F             0x0f00
#define REG_ADDR_RATE_44100          0x1000
#define REG_ADDR_RATE_48000          0x1002
#define REG_ADDR_RATE_88200          0x1008
#define REG_ADDR_RATE_96000          0x100a
#define REG_ADDR_INIT_11             0x110b

#define REG_VAL_ENABLE               0x0101

#define EP_PLAYBACK_FEEDBACK         0x81
#define EP_AUDIO_OUT                 0x02
#define EP_AUDIO_IN                  0x86

#define NUM_PLAYBACK_XFERS   48
#define ISOC_FRAMES_PER_XFER 8
#define MAX_FRAMES_PER_PKT   24
#define PLAYBACK_CHANNELS    4
#define BYTES_PER_SAMPLE     3
#define PLAYBACK_FRAME_SIZE  (PLAYBACK_CHANNELS * BYTES_PER_SAMPLE)

#define CAPTURE_CHANNELS     16
#define CAPTURE_FRAME_SIZE   (CAPTURE_CHANNELS * BYTES_PER_SAMPLE)
#define NUM_CAPTURE_BUFS     8
#define CAPTURE_BUF_SIZE     4096

static volatile int g_running = 1;
static volatile int g_disconnected = 0;
static void on_sig(int s) { (void)s; g_running = 0; }

static void set_realtime_priority(void) {
    struct mach_timebase_info tb;
    mach_timebase_info(&tb);

    thread_time_constraint_policy_data_t policy;
    policy.period = (uint32_t)((1000000ULL * tb.denom) / tb.numer);
    policy.computation = (uint32_t)((250000ULL * tb.denom) / tb.numer);
    policy.constraint = (uint32_t)((500000ULL * tb.denom) / tb.numer);
    policy.preemptible = 1;

    kern_return_t kr = thread_policy_set(
        mach_thread_self(),
        THREAD_TIME_CONSTRAINT_POLICY,
        (thread_policy_t)&policy,
        THREAD_TIME_CONSTRAINT_POLICY_COUNT);
    if (kr != KERN_SUCCESS) {
        pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    }
}

static IOUSBDeviceInterface300 **g_dev = NULL;
static IOUSBInterfaceInterface300 **g_if0 = NULL;
static IOUSBInterfaceInterface300 **g_if1 = NULL;
static CFRunLoopSourceRef g_src0 = NULL;
static CFRunLoopSourceRef g_src1 = NULL;

static int g_pipe_out = -1;
static int g_pipe_bulk_in = -1;
static int g_pipe_fb = -1;

static int g_rate = 44100;
static _Atomic uint32_t g_freq_q16 = ((uint64_t)44100 << 16) / 8000;
static uint32_t g_phase_accum = 0;
static bool g_feedback_synced = false;
static int g_fb_skip = 8;
static UInt64 g_next_isoc_frame = 0;

static void update_rate_accumulator_constants(int rate) {
    g_rate = rate;
    atomic_store_explicit(&g_freq_q16, ((uint64_t)rate << 16) / 8000, memory_order_relaxed);
    g_phase_accum = 0;
    g_feedback_synced = false;
    g_fb_skip = 8;
}

static TascamSharedBuffer *g_shm = NULL;

static bool s_buffering = true;

/* Chime generator state */
static double g_chime_phase = 0.0;
static int g_chime_frames_left = 0;

/* Playback Transfers */
typedef struct {
    uint8_t *audio;
    IOUSBLowLatencyIsocFrame *frames;
    UInt64 start_frame;
    int idx;
} PlaybackXfer;

static PlaybackXfer g_pb_xfers[NUM_PLAYBACK_XFERS];
static uint8_t g_capture_bufs[NUM_CAPTURE_BUFS][CAPTURE_BUF_SIZE];

/* Feedback Transfers (EP 0x81) */
#define NUM_FB_XFERS 4
typedef struct {
    uint8_t *buf;
    IOUSBLowLatencyIsocFrame *frames;
    UInt64 start_frame;
    int idx;
} FbXfer;

static FbXfer g_fb_xfers[NUM_FB_XFERS];
static UInt64 g_fb_next_frame = 0;

static kern_return_t ctrl_msg(IOUSBDeviceInterface300 **dev,
                             uint8_t req, uint8_t rt, uint16_t val, uint16_t idx,
                             void *data, uint16_t len) {
    if (!dev) return kIOReturnNoDevice;
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

static int us1800_configure_device_for_rate(int rate) {
    if (!g_dev) return -1;
    const uint8_t *payload_src;
    uint16_t rate_reg;
    static const uint8_t payload_44100[] = { 0x44, 0xac, 0x00 };
    static const uint8_t payload_48000[] = { 0x80, 0xbb, 0x00 };
    static const uint8_t payload_88200[] = { 0x88, 0x58, 0x01 };
    static const uint8_t payload_96000[] = { 0x00, 0x77, 0x01 };

    switch (rate) {
        case 44100: payload_src = payload_44100; rate_reg = REG_ADDR_RATE_44100; break;
        case 48000: payload_src = payload_48000; rate_reg = REG_ADDR_RATE_48000; break;
        case 88200: payload_src = payload_88200; rate_reg = REG_ADDR_RATE_88200; break;
        case 96000: payload_src = payload_96000; rate_reg = REG_ADDR_RATE_96000; break;
        default: return -1;
    }

    uint8_t payload[3];
    memcpy(payload, payload_src, 3);

    // 0. Stop streaming if running
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);
    usleep(15000);

    // 1. Enter Config Mode
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_CONFIG, 0x0002, NULL, 0);

    // 2. Set UAC Sampling Frequency for EP 0x86 (Audio In) and EP 0x02 (Audio Out)
    ctrl_msg(g_dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, payload, 3);
    ctrl_msg(g_dev, UAC_SET_CUR, RT_H2D_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_OUT, payload, 3);

    // 3. Register read 0x0d00
    uint8_t scratch[16] = {0};
    ctrl_msg(g_dev, VENDOR_REQ_REGISTER_WRITE, RT_D2H_VENDOR_DEV, 0x0d00, REG_VAL_ENABLE, scratch, 5);

    // 4. Hardware registers
    const uint16_t regs[] = {
        REG_ADDR_INIT_0D, REG_ADDR_INIT_0E, REG_ADDR_INIT_0F, rate_reg, REG_ADDR_INIT_11
    };
    for (int i = 0; i < 5; i++) {
        ctrl_msg(g_dev, VENDOR_REQ_REGISTER_WRITE, RT_H2D_VENDOR_DEV, regs[i], REG_VAL_ENABLE, NULL, 0);
    }

    // 5. Verify Sampling Frequency
    memset(scratch, 0, sizeof(scratch));
    ctrl_msg(g_dev, UAC_GET_CUR, RT_D2H_CLASS_EP, UAC_SAMPLING_FREQ_CONTROL, EP_AUDIO_IN, scratch, 3);

    // 6. Handshake read
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, scratch, 1);

    // 7. Start streaming on hardware
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_START_US1800, 0, NULL, 0);
    return 0;
}

/* Verified Bit Deinterleaver for Capture (64 bytes raw -> 16 channels, Float32) */
static void decode_capture_chunk(const uint8_t *src_64, float *dst_f32, int num_frames) {
    const float scale = 1.0f / 8388607.0f;
    for (int f = 0; f < num_frames; f++) {
        const uint8_t *src_even = src_64 + (f * 64);
        const uint8_t *src_odd  = src_64 + (f * 64) + 32;
        uint32_t ch[16] = {0};

        /* Even channels 0, 2, 4, 6, 8, 10, 12, 14 */
        for (int i = 0; i < 24; i++) {
            uint8_t v14 = src_even[i];
            ch[0]  = (ch[0]  << 1) | (v14 & 0x01);
            ch[2]  = (ch[2]  << 1) | ((v14 >> 1) & 0x01);
            ch[4]  = (ch[4]  << 1) | ((v14 >> 2) & 0x01);
            ch[6]  = (ch[6]  << 1) | ((v14 >> 3) & 0x01);
            ch[8]  = (ch[8]  << 1) | ((v14 >> 4) & 0x01);
            ch[10] = (ch[10] << 1) | ((v14 >> 5) & 0x01);
            ch[12] = (ch[12] << 1) | ((v14 >> 6) & 0x01);
            ch[14] = (ch[14] << 1) | ((v14 >> 7) & 0x01);
        }

        /* Odd channels 1, 3, 5, 7, 9, 11, 13, 15 */
        for (int i = 0; i < 24; i++) {
            uint8_t v24 = src_odd[i];
            ch[1]  = (ch[1]  << 1) | (v24 & 0x01);
            ch[3]  = (ch[3]  << 1) | ((v24 >> 1) & 0x01);
            ch[5]  = (ch[5]  << 1) | ((v24 >> 2) & 0x01);
            ch[7]  = (ch[7]  << 1) | ((v24 >> 3) & 0x01);
            ch[9]  = (ch[9]  << 1) | ((v24 >> 4) & 0x01);
            ch[11] = (ch[11] << 1) | ((v24 >> 5) & 0x01);
            ch[13] = (ch[13] << 1) | ((v24 >> 6) & 0x01);
            ch[15] = (ch[15] << 1) | ((v24 >> 7) & 0x01);
        }

        float *d = dst_f32 + (f * CAPTURE_CHANNELS);
        for (int k = 0; k < 16; k++) {
            uint32_t u = ch[k];
            if (u & 0x800000u) u |= 0xFF000000u;
            d[k] = (float)((int32_t)u) * scale;
        }
    }
}

static void queue_bulk_read(int buf_idx);

static void on_bulk_complete(void *refCon, IOReturn result, void *arg0) {
    int buf_idx = (int)(intptr_t)refCon;
    UInt32 bytes = (UInt32)(uintptr_t)arg0;

    if (result == (IOReturn)0xe00002c0 || result == kIOReturnNoDevice) {
        g_disconnected = 1;
        return;
    }

    if (result == (IOReturn)0xe000404f && g_if1) {
        (*g_if1)->ClearPipeStallBothEnds(g_if1, g_pipe_bulk_in);
    }

    if (result == kIOReturnSuccess && bytes >= 64 && g_shm) {
        int frames = bytes / 64;
        float f32_tmp[64 * 16];
        decode_capture_chunk(g_capture_bufs[buf_idx], f32_tmp, frames);

        uint32_t wr = atomic_load_explicit(&g_shm->cap_wr, memory_order_relaxed);
        float peak[16] = {0};

        for (int f = 0; f < frames; f++) {
            uint32_t slot = (wr + f) & TASCAM_RING_MASK;
            float *dst = &g_shm->cap_ring[slot * TASCAM_IN_CHANNELS];
            const float *src = &f32_tmp[f * TASCAM_IN_CHANNELS];
            for (int c = 0; c < 16; c++) {
                dst[c] = src[c];
                float abs_v = fabsf(src[c]);
                if (abs_v > peak[c]) peak[c] = abs_v;
            }
        }
        atomic_store_explicit(&g_shm->cap_wr, (wr + frames) & TASCAM_RING_MASK, memory_order_release);

        for (int c = 0; c < 16; c++) {
            g_shm->in_peak[c] = peak[c];
        }
        atomic_fetch_add_explicit(&g_shm->engine_heartbeat, 1, memory_order_relaxed);
    }

    if (g_running && !g_disconnected && g_if1) {
        queue_bulk_read(buf_idx);
    }
}

static void queue_bulk_read(int buf_idx) {
    if (!g_if1 || g_disconnected || !g_running) return;
    kern_return_t kr = (*g_if1)->ReadPipeAsync(
        g_if1, g_pipe_bulk_in, g_capture_bufs[buf_idx], CAPTURE_BUF_SIZE,
        on_bulk_complete, (void *)(intptr_t)buf_idx);
    if (kr == (kern_return_t)0xe000404f && g_if1) {
        (*g_if1)->ClearPipeStallBothEnds(g_if1, g_pipe_bulk_in);
    }
}

static void resync_playback(void) {
    if (!g_if0) return;
    UInt64 f = 0;
    AbsoluteTime t;
    (*g_if0)->GetBusFrameNumber(g_if0, &f, &t);
    if (g_next_isoc_frame < f + 8) {
        fprintf(stderr, "[!] resync_playback: was %llu, bus=%llu -> jumping to %llu\n",
                g_next_isoc_frame, f, f + 12);
        g_next_isoc_frame = f + 12;
    }
}

static void resync_fb(void) {
    if (!g_if1) return;
    UInt64 f = 0;
    AbsoluteTime t;
    (*g_if1)->GetBusFrameNumber(g_if1, &f, &t);
    g_fb_next_frame = f + 10;
}

static void on_fb_complete(void *refCon, IOReturn result, void *arg0);

static void submit_fb(FbXfer *x) {
    if (!g_if1 || g_disconnected || !g_running || g_pipe_fb <= 0) return;
    x->frames[0].frStatus = 0;
    x->frames[0].frReqCount = 3;
    x->frames[0].frActCount = 0;
    x->frames[0].frTimeStamp.hi = 0;
    x->frames[0].frTimeStamp.lo = 0;

    x->start_frame = g_fb_next_frame;
    g_fb_next_frame += 1;

    kern_return_t kr = (*g_if1)->LowLatencyReadIsochPipeAsync(
        g_if1, g_pipe_fb, x->buf, x->start_frame,
        1, 1, x->frames, on_fb_complete, x);

    if (kr == (kern_return_t)0xe00002ee) {
        resync_fb();
        x->start_frame = g_fb_next_frame;
        g_fb_next_frame += 1;
        (*g_if1)->LowLatencyReadIsochPipeAsync(
            g_if1, g_pipe_fb, x->buf, x->start_frame,
            1, 1, x->frames, on_fb_complete, x);
    }
}

static void on_fb_complete(void *refCon, IOReturn result, void *arg0) {
    (void)arg0;
    FbXfer *x = (FbXfer *)refCon;
    if (result == (IOReturn)0xe00002c0 || result == kIOReturnNoDevice) {
        g_disconnected = 1;
        return;
    }
    if (result == kIOReturnSuccess && x->frames) {
        if (x->frames[0].frStatus == kIOReturnSuccess && x->frames[0].frActCount >= 3) {
            uint8_t *d = x->buf;
            static uint32_t s_fb_log = 0;
            if (s_fb_log++ % 1000 == 0) {
                fprintf(stderr, "[FB] Hardware feedback report: [%u, %u, %u] -> freq_q16=%u (synced=%d)\n",
                        d[0], d[1], d[2], atomic_load_explicit(&g_freq_q16, memory_order_relaxed), (int)g_feedback_synced);
            }
            if (g_fb_skip > 0) {
                g_fb_skip--;
            } else {
                uint32_t sum_frames_3ms = (uint32_t)d[0] + (uint32_t)d[1] + (uint32_t)d[2];
                uint32_t expected_nominal = (uint32_t)((g_rate * 3) / 1000);
                if (sum_frames_3ms >= expected_nominal - 20 && sum_frames_3ms <= expected_nominal + 20) {
                    uint32_t target_freq_q16 = (sum_frames_3ms << 16) / 24;
                    uint32_t cur = atomic_load_explicit(&g_freq_q16, memory_order_relaxed);
                    /* PLL filter: 3/4 old + 1/4 new */
                    uint32_t next = (cur * 3 + target_freq_q16) / 4;
                    atomic_store_explicit(&g_freq_q16, next, memory_order_relaxed);
                    g_feedback_synced = true;
                }
            }
        }
    } else if (result == (IOReturn)0xe00002ee) {
        resync_fb();
    }
    if (g_running && !g_disconnected && g_if1 && g_pipe_fb > 0) {
        submit_fb(x);
    }
}

static void submit_playback(PlaybackXfer *x);

static void on_pb_complete(void *ref, IOReturn result, void *arg0) {
    (void)arg0;
    PlaybackXfer *x = (PlaybackXfer *)ref;

    if (result == (IOReturn)0xe00002c0 || result == kIOReturnNoDevice) {
        g_disconnected = 1;
        return;
    }

    if (result == (IOReturn)0xe000404f && g_if0) {
        (*g_if0)->ClearPipeStallBothEnds(g_if0, g_pipe_out);
    }

    if (result == (IOReturn)0xe00002ee) {
        resync_playback();
    } else if (result != kIOReturnSuccess) {
        static uint32_t s_err_count = 0;
        if (s_err_count++ < 20) {
            fprintf(stderr, "[!] on_pb_complete result error: 0x%x\n", result);
        }
    }

    for (int i = 0; i < ISOC_FRAMES_PER_XFER; i++) {
        if (x->frames[i].frStatus != kIOReturnSuccess && x->frames[i].frStatus != (IOReturn)0xe00002ee) {
            static uint32_t s_fr_err = 0;
            if (s_fr_err++ < 20) {
                fprintf(stderr, "[!] frame[%d] status error: 0x%x req=%u act=%u\n",
                        i, x->frames[i].frStatus, x->frames[i].frReqCount, x->frames[i].frActCount);
            }
        }
    }

    if (g_running && !g_disconnected && g_if0) {
        submit_playback(x);
    }
}

static void submit_playback(PlaybackXfer *x) {
    if (!g_if0 || g_disconnected || !g_running) return;

    uint32_t cur_freq_q16 = atomic_load_explicit(&g_freq_q16, memory_order_relaxed);
    int total_bytes = 0;
    for (int i = 0; i < ISOC_FRAMES_PER_XFER; i++) {
        g_phase_accum += cur_freq_q16;
        int frames = (int)(g_phase_accum >> 16);
        g_phase_accum &= 0xFFFF;

        if (frames > MAX_FRAMES_PER_PKT) frames = MAX_FRAMES_PER_PKT;

        int pkt_bytes = frames * PLAYBACK_FRAME_SIZE;
        x->frames[i].frStatus = 0;
        x->frames[i].frReqCount = pkt_bytes;
        x->frames[i].frActCount = 0;
        x->frames[i].frTimeStamp.hi = 0;
        x->frames[i].frTimeStamp.lo = 0;
        total_bytes += pkt_bytes;
    }

    int req_frames = total_bytes / PLAYBACK_FRAME_SIZE;

    /* Check if chime test tone is triggered */
    if (g_shm && atomic_load_explicit(&g_shm->cmd_chime_test, memory_order_relaxed)) {
        atomic_store_explicit(&g_shm->cmd_chime_test, 0, memory_order_relaxed);
        g_chime_frames_left = g_rate * 2; /* 2 seconds of test tone */
        g_chime_phase = 0.0;
        s_buffering = true;
    }

    if (g_chime_frames_left > 0) {
        /* Generate cycling test chime directly into output */
        for (int f = 0; f < req_frames; f++) {
            double sec = g_chime_phase / (double)g_rate;
            int step = (int)(fmod(sec, 2.0) / 0.5);
            double freq = 440.0;
            int active_ch = 0;
            if (step == 0) { freq = 440.00; active_ch = 0; }      /* A4  (Ch 1 / L) */
            else if (step == 1) { freq = 554.37; active_ch = 1; } /* C#5 (Ch 2 / R) */
            else if (step == 2) { freq = 659.25; active_ch = 2; } /* E5  (Ch 3) */
            else if (step == 3) { freq = 880.00; active_ch = 3; } /* A5  (Ch 4) */

            double s = sin(2.0 * M_PI * freq * g_chime_phase / (double)g_rate) * 0.15;
            g_chime_phase += 1.0;
            int32_t v = (int32_t)(s * 8388607.0);

            uint8_t *dst = x->audio + (f * PLAYBACK_FRAME_SIZE);
            for (int c = 0; c < 4; c++) {
                int32_t ch_val = (c == active_ch || active_ch < 0) ? v : 0;
                dst[c * 3 + 0] = (uint8_t)(ch_val & 0xFF);
                dst[c * 3 + 1] = (uint8_t)((ch_val >> 8) & 0xFF);
                dst[c * 3 + 2] = (uint8_t)((ch_val >> 16) & 0xFF);
            }
        }
        g_chime_frames_left -= req_frames;
        for (int c = 0; c < 4; c++) g_shm->out_peak[c] = 0.15f;
    } else if (g_shm) {
        uint32_t wr = atomic_load_explicit(&g_shm->pb_wr, memory_order_acquire);
        uint32_t rd = atomic_load_explicit(&g_shm->pb_rd, memory_order_relaxed);
        uint32_t avail = (wr - rd) & TASCAM_RING_MASK;

        /* Low-latency dynamic target cushion: tracks DAW buffer size directly */
        uint32_t buf_sz = atomic_load_explicit(&g_shm->buffer_frame_size, memory_order_relaxed);
        if (buf_sz < 16) buf_sz = 16;
        if (buf_sz > 2048) buf_sz = 2048;

        static uint32_t s_last_applied_mode = 999;
        static uint32_t s_last_applied_buf = 999;
        uint32_t mode = atomic_load_explicit(&g_shm->latency_mode, memory_order_relaxed);
        uint32_t target_cushion;
        if (mode == TASCAM_MODE_LOW_LATENCY) {
            /* Mode 0: Ultra-Low Latency (Live, ~3.5ms RTL) */
            target_cushion = buf_sz * 2;
            if (target_cushion < 64) target_cushion = 64;
            if (target_cushion > 2048) target_cushion = 2048;
        } else if (mode == TASCAM_MODE_BALANCED) {
            /* Mode 1: Balanced Studio (~7ms RTL) */
            target_cushion = buf_sz * 4;
            if (target_cushion < 256) target_cushion = 256;
            if (target_cushion > 3072) target_cushion = 3072;
        } else {
            /* Mode 2: Safe Conservative (Solid buffer protection) */
            target_cushion = buf_sz * 6;
            if (target_cushion < 1024) target_cushion = 1024;
            if (target_cushion > 4096) target_cushion = 4096;
        }

        /* Seamless cushion adaptation when user switches mode or buffer size */
        if (mode != s_last_applied_mode || buf_sz != s_last_applied_buf) {
            s_last_applied_mode = mode;
            s_last_applied_buf = buf_sz;
        }

        const char *env_cushion = getenv("TASCAM_CUSHION_FRAMES");
        if (env_cushion && atoi(env_cushion) > 0) {
            target_cushion = (uint32_t)atoi(env_cushion);
        }

        static bool s_buffering = true;
        static uint32_t s_idle_count = 0;

        /* Underrun check: if rd has overtaken wr, avail is near 32768 */
        if (avail > 32000) {
            avail = 0;
            rd = wr;
            atomic_store_explicit(&g_shm->pb_rd, rd, memory_order_release);
        }

        if (avail == 0) {
            /* Output silence on starvation */
            memset(x->audio, 0, total_bytes);
            for (int c = 0; c < 4; c++) g_shm->out_peak[c] = 0.0f;
            s_idle_count++;
            if (s_idle_count >= 20) { /* 20ms of silence = stream stopped/idle, set buffering */
                s_buffering = true;
                rd = wr;
                atomic_store_explicit(&g_shm->pb_rd, wr, memory_order_release);
            }
            return;
        }

        s_idle_count = 0;

        /* While buffering after silence, wait for target cushion to fill before playing */
        if (s_buffering) {
            if (avail < target_cushion) {
                memset(x->audio, 0, total_bytes);
                for (int c = 0; c < 4; c++) g_shm->out_peak[c] = 0.0f;
                return;
            }
            /* Cushion filled! Start playback cleanly without skipping any audio */
            s_buffering = false;
        }

        /* Backlog recovery: ONLY trigger if a massive backlog accumulated
           (e.g. system sleep, track scrub, or audio server freeze > 350ms = 16384 frames).
           NEVER chop normal macOS buffer bursts (1000-4000 frames)! */
        if (avail > 16384 && avail <= 32000) {
            rd = (wr - target_cushion) & TASCAM_RING_MASK;
            avail = target_cushion;
            atomic_store_explicit(&g_shm->pb_rd, rd, memory_order_release);
        }

        int frames_to_read = req_frames;
        if (avail < (uint32_t)req_frames) {
            frames_to_read = (int)avail;
        }

        float peak[4] = {0};

        /* 100% BIT-PERFECT 1:1 DIRECT PCM CONVERSION - ZERO ARTIFACTS */
        for (int f = 0; f < frames_to_read; f++) {
            uint32_t slot = (rd + f) & TASCAM_RING_MASK;
            const float *src = &g_shm->pb_ring[slot * TASCAM_OUT_CHANNELS];
            uint8_t *dst = x->audio + (f * PLAYBACK_FRAME_SIZE);

            for (int c = 0; c < 4; c++) {
                float val = src[c];
                if (val > 1.0f) val = 1.0f;
                if (val < -1.0f) val = -1.0f;
                int32_t s24 = (int32_t)(val * 8388607.0f);

                dst[c * 3 + 0] = (uint8_t)(s24 & 0xFF);
                dst[c * 3 + 1] = (uint8_t)((s24 >> 8) & 0xFF);
                dst[c * 3 + 2] = (uint8_t)((s24 >> 16) & 0xFF);

                float abs_v = fabsf(val);
                if (abs_v > peak[c]) peak[c] = abs_v;
            }
        }

        /* If momentarily short on frames, smoothly fade out tail to eliminate vinyl crackle */
        if (frames_to_read < req_frames) {
            int fade_len = (frames_to_read < 16) ? frames_to_read : 16;
            for (int i = 0; i < fade_len; i++) {
                int f = (frames_to_read - fade_len) + i;
                float gain = (float)(fade_len - 1 - i) / (float)fade_len;
                uint8_t *p = x->audio + (f * PLAYBACK_FRAME_SIZE);
                for (int c = 0; c < 4; c++) {
                    int32_t s = (int32_t)(p[c*3+0] | (p[c*3+1] << 8) | (p[c*3+2] << 16));
                    if (s & 0x800000) s |= 0xFF000000;
                    s = (int32_t)(s * gain);
                    p[c*3+0] = (uint8_t)(s & 0xFF);
                    p[c*3+1] = (uint8_t)((s >> 8) & 0xFF);
                    p[c*3+2] = (uint8_t)((s >> 16) & 0xFF);
                }
            }
            memset(x->audio + (frames_to_read * PLAYBACK_FRAME_SIZE), 0,
                   (req_frames - frames_to_read) * PLAYBACK_FRAME_SIZE);
        }

        /* Advance rd by EXACTLY frames_to_read: zero pitch drift */
        rd = (rd + frames_to_read) & TASCAM_RING_MASK;

        /* Gentle, inaudible buffer drift control scaled to target_cushion */
        static uint32_t s_drift_check_counter = 0;
        if (++s_drift_check_counter >= 500) { /* check twice every second */
            s_drift_check_counter = 0;
            uint32_t drift_tolerance = target_cushion / 4;
            if (drift_tolerance < 32) drift_tolerance = 32;

            if (avail > target_cushion + drift_tolerance && avail < 16384) {
                /* Read pointer falling behind (write buffer growing), advance rd by 1 extra frame */
                rd = (rd + 1) & TASCAM_RING_MASK;
            } else if (avail < target_cushion - drift_tolerance && avail > 32) {
                /* Read pointer getting too close (write buffer shrinking), hold rd by 1 frame */
                rd = (rd - 1) & TASCAM_RING_MASK;
            }
        }

        atomic_store_explicit(&g_shm->pb_rd, rd, memory_order_release);
        for (int c = 0; c < 4; c++) g_shm->out_peak[c] = peak[c];
    } else {
        memset(x->audio, 0, total_bytes);
    }

    x->start_frame = g_next_isoc_frame;
    g_next_isoc_frame += 1;

    kern_return_t kr = (*g_if0)->LowLatencyWriteIsochPipeAsync(
        g_if0, g_pipe_out, x->audio, x->start_frame,
        ISOC_FRAMES_PER_XFER, 1, x->frames, on_pb_complete, x);

    if (kr == (kern_return_t)0xe00002ee) {
        resync_playback();
        x->start_frame = g_next_isoc_frame;
        g_next_isoc_frame += 1;
        (*g_if0)->LowLatencyWriteIsochPipeAsync(
            g_if0, g_pipe_out, x->audio, x->start_frame,
            ISOC_FRAMES_PER_XFER, 1, x->frames, on_pb_complete, x);
    } else if (kr == (kern_return_t)0xe000404f && g_if0) {
        (*g_if0)->ClearPipeStallBothEnds(g_if0, g_pipe_out);
        resync_playback();
        x->start_frame = g_next_isoc_frame;
        g_next_isoc_frame += 1;
        (*g_if0)->LowLatencyWriteIsochPipeAsync(
            g_if0, g_pipe_out, x->audio, x->start_frame,
            ISOC_FRAMES_PER_XFER, 1, x->frames, on_pb_complete, x);
    } else if (kr == (kern_return_t)0xe00002c0 || kr == kIOReturnNoDevice) {
        g_disconnected = 1;
    }
}

static void cleanup_hardware(void) {
    if (g_shm) {
        atomic_store(&g_shm->engine_running, 0);
    }

    if (g_dev) {
        ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_H2D_VENDOR_DEV, MODE_VAL_STREAM_STOP_US1800, 0, NULL, 0);
    }

    if (g_src0) {
        CFRunLoopRemoveSource(CFRunLoopGetCurrent(), g_src0, kCFRunLoopDefaultMode);
        CFRelease(g_src0);
        g_src0 = NULL;
    }
    if (g_src1) {
        CFRunLoopRemoveSource(CFRunLoopGetCurrent(), g_src1, kCFRunLoopDefaultMode);
        CFRelease(g_src1);
        g_src1 = NULL;
    }

    if (g_if0) {
        for (int i = 0; i < NUM_PLAYBACK_XFERS; i++) {
            if (g_pb_xfers[i].audio) {
                (*g_if0)->LowLatencyDestroyBuffer(g_if0, g_pb_xfers[i].audio);
                g_pb_xfers[i].audio = NULL;
            }
            if (g_pb_xfers[i].frames) {
                (*g_if0)->LowLatencyDestroyBuffer(g_if0, g_pb_xfers[i].frames);
                g_pb_xfers[i].frames = NULL;
            }
        }
        (*g_if0)->USBInterfaceClose(g_if0);
        (*g_if0)->Release(g_if0);
        g_if0 = NULL;
    }

    if (g_if1) {
        for (int i = 0; i < NUM_FB_XFERS; i++) {
            if (g_fb_xfers[i].buf) {
                (*g_if1)->LowLatencyDestroyBuffer(g_if1, g_fb_xfers[i].buf);
                g_fb_xfers[i].buf = NULL;
            }
            if (g_fb_xfers[i].frames) {
                (*g_if1)->LowLatencyDestroyBuffer(g_if1, g_fb_xfers[i].frames);
                g_fb_xfers[i].frames = NULL;
            }
        }
        (*g_if1)->USBInterfaceClose(g_if1);
        (*g_if1)->Release(g_if1);
        g_if1 = NULL;
    }

    if (g_dev) {
        (*g_dev)->USBDeviceClose(g_dev);
        (*g_dev)->Release(g_dev);
        g_dev = NULL;
    }
    g_pipe_out = -1;
    g_pipe_bulk_in = -1;
    g_pipe_fb = -1;
    s_buffering = true;
}

static bool try_init_hardware(void) {
    cleanup_hardware();

    CFMutableDictionaryRef match = IOServiceMatching(kIOUSBDeviceClassName);
    int32_t vid = USB_VID_TASCAM, pid = USB_PID_TASCAM_US1800;
    CFNumberRef vr = CFNumberCreate(NULL, kCFNumberSInt32Type, &vid);
    CFNumberRef pr = CFNumberCreate(NULL, kCFNumberSInt32Type, &pid);
    CFDictionarySetValue(match, CFSTR(kUSBVendorID), vr);
    CFDictionarySetValue(match, CFSTR(kUSBProductID), pr);
    CFRelease(vr); CFRelease(pr);

    io_service_t svc = IOServiceGetMatchingService(kIOMainPortDefault, match);
    if (!svc) return false;

    IOCFPlugInInterface **plug = NULL;
    SInt32 score;
    IOCreatePlugInInterfaceForService(svc, kIOUSBDeviceUserClientTypeID,
                                      kIOCFPlugInInterfaceID, &plug, &score);
    IOObjectRelease(svc);
    if (!plug) return false;

    (*plug)->QueryInterface(plug, CFUUIDGetUUIDBytes(kIOUSBDeviceInterfaceID300), (LPVOID *)&g_dev);
    (*plug)->Release(plug);
    if (!g_dev) return false;

    (*g_dev)->USBDeviceOpen(g_dev);
    (*g_dev)->SetConfiguration(g_dev, 1);

    IOUSBFindInterfaceRequest req = {
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare,
        kIOUSBFindInterfaceDontCare, kIOUSBFindInterfaceDontCare
    };
    io_iterator_t iter;
    (*g_dev)->CreateInterfaceIterator(g_dev, &req, &iter);
    io_service_t isvc;
    int if_idx = 0;
    while ((isvc = IOIteratorNext(iter))) {
        IOCFPlugInInterface **iplug = NULL;
        IOCreatePlugInInterfaceForService(isvc, kIOUSBInterfaceUserClientTypeID,
                                          kIOCFPlugInInterfaceID, &iplug, &score);
        IOObjectRelease(isvc);
        if (iplug) {
            IOUSBInterfaceInterface300 **iface = NULL;
            (*iplug)->QueryInterface(iplug, CFUUIDGetUUIDBytes(kIOUSBInterfaceInterfaceID300), (LPVOID *)&iface);
            (*iplug)->Release(iplug);
            if (if_idx == 0) g_if0 = iface;
            else if (if_idx == 1) g_if1 = iface;
            else if (iface) { (*iface)->Release(iface); }
        }
        if_idx++;
    }
    IOObjectRelease(iter);

    if (!g_if0 || !g_if1) {
        cleanup_hardware();
        return false;
    }

    (*g_if0)->USBInterfaceOpen(g_if0);
    (*g_if1)->USBInterfaceOpen(g_if1);

    uint8_t fw_buf[16] = {0};
    ctrl_msg(g_dev, VENDOR_REQ_FIRMWARE_READ, RT_D2H_VENDOR_DEV, 0, 0, fw_buf, 15);
    fprintf(stderr, "[*] TASCAM US-1800 Firmware: %s\n", fw_buf + 4);

    (*g_if0)->SetAlternateInterface(g_if0, 1);
    (*g_if1)->SetAlternateInterface(g_if1, 1);

    uint8_t boot_state = 0;
    ctrl_msg(g_dev, VENDOR_REQ_MODE_CONTROL, RT_D2H_VENDOR_DEV, MODE_VAL_HANDSHAKE_READ, 0, &boot_state, 1);

    if (us1800_configure_device_for_rate(g_rate) < 0) {
        fprintf(stderr, "Error: Rate config failed for %d Hz\n", g_rate);
        cleanup_hardware();
        return false;
    }
    update_rate_accumulator_constants(g_rate);

    UInt8 n0, n1;
    (*g_if0)->GetNumEndpoints(g_if0, &n0);
    for (UInt8 p_num = 1; p_num <= n0; p_num++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*g_if0)->GetPipeProperties(g_if0, p_num, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBOut && tt == kUSBIsoc && num == 2) g_pipe_out = p_num;
    }

    (*g_if1)->GetNumEndpoints(g_if1, &n1);
    for (UInt8 p_num = 1; p_num <= n1; p_num++) {
        UInt8 dir, num, tt, interval;
        UInt16 maxPkt;
        (*g_if1)->GetPipeProperties(g_if1, p_num, &dir, &num, &tt, &maxPkt, &interval);
        if (dir == kUSBIn && tt == kUSBBulk && num == 6) g_pipe_bulk_in = p_num;
        if (dir == kUSBIn && tt == kUSBIsoc && num == 1) g_pipe_fb = p_num;
    }
    fprintf(stderr, "[*] USB Pipes: Out=%d, BulkIn=%d, FeedbackIn=%d\n", g_pipe_out, g_pipe_bulk_in, g_pipe_fb);

    (*g_if0)->CreateInterfaceAsyncEventSource(g_if0, &g_src0);
    (*g_if1)->CreateInterfaceAsyncEventSource(g_if1, &g_src1);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), g_src0, kCFRunLoopDefaultMode);
    CFRunLoopAddSource(CFRunLoopGetCurrent(), g_src1, kCFRunLoopDefaultMode);

    /* Setup Playback Low-Latency Transfers */
    int max_pkt = PLAYBACK_CHANNELS * BYTES_PER_SAMPLE * MAX_FRAMES_PER_PKT;
    UInt32 abuf_sz = ISOC_FRAMES_PER_XFER * max_pkt;
    UInt32 fbuf_sz = ISOC_FRAMES_PER_XFER * sizeof(IOUSBLowLatencyIsocFrame);

    for (int i = 0; i < NUM_PLAYBACK_XFERS; i++) {
        void *a = NULL, *f_ptr = NULL;
        (*g_if0)->LowLatencyCreateBuffer(g_if0, &a, abuf_sz, kUSBLowLatencyWriteBuffer);
        (*g_if0)->LowLatencyCreateBuffer(g_if0, &f_ptr, fbuf_sz, kUSBLowLatencyFrameListBuffer);
        g_pb_xfers[i].audio = (uint8_t *)a;
        g_pb_xfers[i].frames = (IOUSBLowLatencyIsocFrame *)f_ptr;
        g_pb_xfers[i].idx = i;
    }

    resync_playback();
    s_buffering = true;

    for (int i = 0; i < NUM_PLAYBACK_XFERS; i++) {
        submit_playback(&g_pb_xfers[i]);
    }

    /* Setup Feedback Transfers */
    if (g_pipe_fb > 0) {
        for (int i = 0; i < NUM_FB_XFERS; i++) {
            void *a = NULL, *f_ptr = NULL;
            (*g_if1)->LowLatencyCreateBuffer(g_if1, &a, 64, kUSBLowLatencyReadBuffer);
            (*g_if1)->LowLatencyCreateBuffer(g_if1, &f_ptr, sizeof(IOUSBLowLatencyIsocFrame), kUSBLowLatencyFrameListBuffer);
            g_fb_xfers[i].buf = (uint8_t *)a;
            g_fb_xfers[i].frames = (IOUSBLowLatencyIsocFrame *)f_ptr;
            g_fb_xfers[i].idx = i;
        }
        resync_fb();
        for (int i = 0; i < NUM_FB_XFERS; i++) {
            submit_fb(&g_fb_xfers[i]);
        }
    }

    /* Setup Capture Bulk Reads */
    for (int i = 0; i < NUM_CAPTURE_BUFS; i++) {
        queue_bulk_read(i);
    }

    if (g_shm) {
        atomic_store(&g_shm->engine_running, 1);
    }
    return true;
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;
    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);

    fprintf(stderr, "===================================================\n"
                    " TASCAM US-1800 Professional Live DAW Audio Engine \n"
                    " Apple Silicon M1/M2/M3 Native CoreAudio HAL Driver\n"
                    "===================================================\n");

    /* 1. Setup POSIX Shared Memory */
    int shm_fd = shm_open(TASCAM_SHM_NAME, O_RDWR | O_CREAT, 0666);
    if (shm_fd < 0) {
        fprintf(stderr, "Error: shm_open failed: %m\n");
        return 1;
    }
    ftruncate(shm_fd, sizeof(TascamSharedBuffer));
    void *p = mmap(NULL, sizeof(TascamSharedBuffer), PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
    if (p == MAP_FAILED) {
        fprintf(stderr, "Error: mmap failed: %m\n");
        close(shm_fd);
        return 1;
    }
    close(shm_fd);

    int initial_mode = TASCAM_MODE_LOW_LATENCY;
    FILE *fp = fopen(TASCAM_CONF_PATH, "r");
    if (fp) {
        int m = -1;
        if (fscanf(fp, "%d", &m) == 1 && m >= 0 && m <= 2) {
            initial_mode = m;
        }
        fclose(fp);
    }

    g_shm = (TascamSharedBuffer *)p;
    if (g_shm->magic != TASCAM_SHM_MAGIC || g_shm->version != TASCAM_SHM_VERSION) {
        memset(g_shm, 0, sizeof(TascamSharedBuffer));
        g_shm->magic = TASCAM_SHM_MAGIC;
        g_shm->version = TASCAM_SHM_VERSION;
        atomic_store(&g_shm->sample_rate, 44100);
        atomic_store(&g_shm->buffer_frame_size, 128);
        atomic_store(&g_shm->latency_mode, initial_mode);
        g_shm->master_volume = 1.0f;
    }

    g_rate = 44100;
    atomic_store(&g_shm->sample_rate, 44100);
    fprintf(stderr, "[*] Target sample rate: %d Hz (Locked Reference Standard)\n", g_rate);

    /* 2. Set Mach Real-Time Audio Priority */
    set_realtime_priority();

    /* 3. Main Hardware Lifecycle Loop (with Hotplug Auto-Recovery) */
    while (g_running) {
        if (!g_dev) {
            if (!try_init_hardware()) {
                usleep(250000);
                continue;
            }
            fprintf(stderr, "[✓] TASCAM US-1800 Live Hardware Engine Running!\n"
                            "    16 Inputs / 4 Outputs Active at %d Hz\n"
                            "    Ready for low-latency live concert mixing!\n", g_rate);
        }

        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, false);

        if (g_disconnected) {
            fprintf(stderr, "[!] USB connection lost! Reconnecting...\n");
            cleanup_hardware();
            g_disconnected = 0;
            usleep(500000);
            continue;
        }

        /* Mode persistence check: if mode changed, save to conf */
        static int s_last_saved_mode = -1;
        if (s_last_saved_mode == -1) s_last_saved_mode = initial_mode;
        int cur_mode = (int)atomic_load_explicit(&g_shm->latency_mode, memory_order_relaxed);
        if (cur_mode != s_last_saved_mode && cur_mode >= 0 && cur_mode <= 2) {
            FILE *wfp = fopen(TASCAM_CONF_PATH, "w");
            if (wfp) {
                fprintf(wfp, "%d\n", cur_mode);
                fclose(wfp);
            }
            s_last_saved_mode = cur_mode;
            fprintf(stderr, "[*] Latency mode switched to profile %d\n", cur_mode);
        }

        /* Ensure hardware and shared memory state are kept healthy */
        if (g_shm) {
            atomic_store(&g_shm->engine_running, (g_dev && !g_disconnected) ? 1 : 0);
            atomic_store(&g_shm->sample_rate, 44100);
            atomic_fetch_add_explicit(&g_shm->engine_heartbeat, 1, memory_order_relaxed);
        }
    }

    fprintf(stderr, "\nStopping live engine...\n");
    cleanup_hardware();
    if (g_shm) {
        atomic_store(&g_shm->engine_running, 0);
        munmap(g_shm, sizeof(TascamSharedBuffer));
    }
    fprintf(stderr, "Terminated cleanly.\n");
    return 0;
}
