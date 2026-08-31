/* Reproducer for the devrc-ci store-api gate failure.
 *
 * CI evidence says the server thread blocks in fsync() long enough that the
 * client's 60s socket read times out, and the harness classifies it
 * SERVER_BLOCKED_IN_FSYNC. This shim reproduces exactly that condition without
 * modifying a single repo file: it delays the FIRST fsync() in each process
 * past HANG_TIMEOUT (60.0) and lets every later call through untouched.
 *
 * 🔴 PER PROCESS, NOT PER RUN. LD_PRELOAD is inherited across exec(), and the
 * latch below is ordinary process memory, so every child — each xdist worker,
 * every `git`/`bash`/`nix` subprocess — gets its OWN 65s stall. Measured: a
 * two-process control stalled 65.0s in the parent AND 65.0s in the child.
 * Preload it for ONE test selection, never for a whole-file or whole-suite run.
 *
 * It reports the elapsed stall it actually achieved rather than the one it
 * asked for: a signal delivered to the stalling thread would otherwise cut the
 * stall short and print an identical line, turning an under-delivered stall
 * into a PASSING run that reads as "not reproducible".
 *
 * Build:  gcc -shared -fPIC -o slowfsync.so slowfsync.c -ldl
 * Use:    LD_PRELOAD=/abs/path/slowfsync.so pytest ...
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <unistd.h>
#include <stdio.h>
#include <time.h>
#include <errno.h>

/* The server under test is multi-threaded, so the latch is atomic: a plain int
 * is a C11 data race and could spend a second 65s stall on another thread. */
static volatile int stalled = 0;

#define STALL_SECONDS 65

int fsync(int fd) {
    static int (*real)(int) = NULL;
    if (!real) {
        *(void **)(&real) = dlsym(RTLD_NEXT, "fsync");
        if (!real) {
            /* Unreachable under LD_PRELOAD (RTLD_NEXT always finds libc's
             * fsync), but a dlopen'd misuse should fail loudly, not SIGSEGV. */
            fprintf(stderr, "[slowfsync] FATAL: dlsym(RTLD_NEXT, \"fsync\") failed: %s\n",
                    dlerror() ? dlerror() : "(no error)");
            errno = ENOSYS;
            return -1;
        }
    }
    if (!__atomic_test_and_set(&stalled, __ATOMIC_SEQ_CST)) {
        struct timespec t0, t1, rem;
        fprintf(stderr, "[slowfsync] stalling fsync(%d) for %ds (HANG_TIMEOUT=60), pid=%d\n",
                fd, STALL_SECONDS, (int)getpid());
        fflush(stderr);
        clock_gettime(CLOCK_MONOTONIC, &t0);
        rem.tv_sec = STALL_SECONDS;
        rem.tv_nsec = 0;
        /* Resume on EINTR: a signal must not silently shorten the stall. */
        while (nanosleep(&rem, &rem) == -1 && errno == EINTR) { }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        fprintf(stderr, "[slowfsync] stall released after %.1fs (asked %ds) pid=%d\n",
                (double)(t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9,
                STALL_SECONDS, (int)getpid());
        fflush(stderr);
    }
    return real(fd);
}
