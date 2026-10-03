#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <time.h>
#include "tascam_shm.h"

int main(int argc, char *argv[]) {
    int duration = (argc > 1) ? atoi(argv[1]) : 30;
    int shm_fd = shm_open(TASCAM_SHM_NAME, O_RDONLY, 0666);
    if (shm_fd < 0) {
        perror("shm_open");
        return 1;
    }
    TascamSharedBuffer *shm = (TascamSharedBuffer *)mmap(NULL, sizeof(TascamSharedBuffer), PROT_READ, MAP_SHARED, shm_fd, 0);
    if (shm == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    printf("=== Accurate TASCAM Stream Diagnostic (%d seconds) ===\n", duration);
    printf("Sample Rate: %u Hz | Buffer Size: %u frames | Engine: %s\n",
           shm->sample_rate, shm->buffer_frame_size,
           shm->engine_running ? "RUNNING" : "STOPPED");

    uint32_t last_wr = atomic_load(&shm->pb_wr);
    uint32_t last_rd = atomic_load(&shm->pb_rd);
    uint64_t last_hb = atomic_load(&shm->engine_heartbeat);

    uint32_t min_avail = 999999, max_avail = 0;
    int underruns = 0;

    for (int s = 1; s <= duration; s++) {
        sleep(1);
        uint32_t wr = atomic_load(&shm->pb_wr);
        uint32_t rd = atomic_load(&shm->pb_rd);
        uint64_t hb = atomic_load(&shm->engine_heartbeat);

        uint32_t avail = (wr - rd) & TASCAM_RING_MASK;
        int d_wr = (int)((wr - last_wr) & TASCAM_RING_MASK);
        int d_rd = (int)((rd - last_rd) & TASCAM_RING_MASK);
        int fps_wr = (d_wr > 5000) ? (d_wr + TASCAM_RING_FRAMES) : d_wr;
        int fps_rd = (d_rd > 5000) ? (d_rd + TASCAM_RING_FRAMES) : d_rd;

        if (avail < min_avail) min_avail = avail;
        if (avail > max_avail) max_avail = avail;
        if (avail == 0 || avail > 30000) underruns++;

        printf("[%2ds] wr_in=%5d rd_out=%5d | net_drift=%+4d | avail=%4u (min=%4u max=%4u) | peak=[%.2f, %.2f] | underruns=%d\n",
               s, fps_wr, fps_rd, d_wr - d_rd, avail, min_avail, max_avail,
               shm->out_peak[0], shm->out_peak[1], underruns);

        last_wr = wr;
        last_rd = rd;
        last_hb = hb;
    }

    printf("=== Summary: min_avail=%u max_avail=%u total_underruns=%d ===\n",
           min_avail, max_avail, underruns);
    return 0;
}
