#ifndef TASCAM_SHM_H
#define TASCAM_SHM_H

#include <stdint.h>
#include <stdatomic.h>

#define TASCAM_SHM_NAME             "/tascam_us1800_shm"
#define TASCAM_SHM_MAGIC            0x54313830 // 'T180'
#define TASCAM_SHM_VERSION          3

#define TASCAM_IN_CHANNELS          16
#define TASCAM_OUT_CHANNELS         4

/* Latency Profiles switchable live from GUI / Control Panel */
#define TASCAM_MODE_LOW_LATENCY     0  /* Ultra-Low Latency (Live Guitar/Vocal Monitoring, ~3.5ms RTL) */
#define TASCAM_MODE_BALANCED        1  /* Balanced Studio (Recording & Mixing, ~7ms RTL) */
#define TASCAM_MODE_SAFE            2  /* Safe Conservative (Heavy DAW projects, background audio, 46ms RTL) */

#define TASCAM_CONF_PATH            "/var/tmp/tascam_mode.conf"

/* 32768 frames is ~0.74s at 44.1kHz, fits within macOS 4MB shmmax limit */
#define TASCAM_RING_FRAMES          32768
#define TASCAM_RING_MASK            (TASCAM_RING_FRAMES - 1)

typedef struct {
    uint32_t magic;
    uint32_t version;
    _Atomic uint32_t sample_rate;       /* 44100, 48000, 88200, 96000 */
    _Atomic uint32_t buffer_frame_size; /* 16, 32, 64, 128, 256, 512, 1024 */
    _Atomic uint64_t engine_heartbeat;  /* counter from live engine */
    _Atomic uint32_t engine_running;    /* 1 = engine active, 0 = stopped */

    /* Playback ring buffer (HAL plugin writes Float32 4-ch, Live engine reads and writes to USB) */
    _Atomic uint32_t pb_wr;
    _Atomic uint32_t pb_rd;
    float pb_ring[TASCAM_RING_FRAMES * TASCAM_OUT_CHANNELS];

    /* Capture ring buffer (Live engine reads USB 16-ch, decodes to Float32, HAL plugin reads) */
    _Atomic uint32_t cap_wr;
    _Atomic uint32_t cap_rd;
    float cap_ring[TASCAM_RING_FRAMES * TASCAM_IN_CHANNELS];

    /* Real-time peak meters (0.0f - 1.0f) for GUI */
    float in_peak[TASCAM_IN_CHANNELS];
    float out_peak[TASCAM_OUT_CHANNELS];

    /* GUI commands & latency profile */
    _Atomic uint32_t cmd_chime_test;     /* 1 = play chime, engine clears after playing */
    _Atomic uint32_t master_mute;        /* 0 = unmuted, 1 = muted */
    float master_volume;                 /* 0.0f to 1.0f */
    _Atomic uint32_t latency_mode;       /* 0 = Low Latency, 1 = Balanced, 2 = Safe */
} TascamSharedBuffer;

#endif /* TASCAM_SHM_H */

#ifndef kAudioDevicePropertyBufferFrameSize
#define kAudioDevicePropertyBufferFrameSize              0x6673697a /* 'fsiz' */
#endif
#ifndef kAudioDevicePropertyBufferFrameSizeRange
#define kAudioDevicePropertyBufferFrameSizeRange         0x66737a23 /* 'fsz#' */
#endif
#ifndef kAudioDevicePropertyUsesVariableBufferFrameSizes
#define kAudioDevicePropertyUsesVariableBufferFrameSizes 0x7666737a /* 'vfsz' */
#endif
