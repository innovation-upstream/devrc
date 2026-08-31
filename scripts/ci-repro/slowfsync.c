/* Reproducer for the devrc-ci pytests flake.
 *
 * CI evidence says the server thread blocks in fsync() long enough that the
 * client's 60s socket read times out, and the harness classifies it
 * SERVER_BLOCKED_IN_FSYNC. This shim reproduces exactly that condition without
 * modifying a single repo file: it delays the FIRST fsync() call past
 * HANG_TIMEOUT (60.0) and lets every later call through untouched, so the run
 * costs one stall rather than one per fsync.
 *
 * Build:  gcc -shared -fPIC -o slowfsync.so slowfsync.c -ldl
 * Use:    LD_PRELOAD=./slowfsync.so pytest ...
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <unistd.h>
#include <stdio.h>

static int stalled = 0;

int fsync(int fd) {
    static int (*real)(int) = NULL;
    if (!real) real = (int (*)(int))dlsym(RTLD_NEXT, "fsync");
    if (!stalled) {
        stalled = 1;
        fprintf(stderr, "[slowfsync] stalling fsync(%d) for 65s (HANG_TIMEOUT=60)\n", fd);
        fflush(stderr);
        sleep(65);
        fprintf(stderr, "[slowfsync] stall released\n");
        fflush(stderr);
    }
    return real(fd);
}
