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
 * 🔴 `SLOWFSYNC_SKIP_TMPFS=1` — THE FILESYSTEM-AWARE MODE, AND IT EXISTS
 * BECAUSE THE DEFAULT MODE CANNOT MEASURE A SITING FIX AT ALL. This shim
 * intercepts fsync(2) in libc, so it stalls whatever the fd is backed by. That
 * is right for "does a slow fsync fail this test" and WRONG for "does siting
 * the store off the contended disk fix it": with the default mode both arms of
 * that comparison go red, and the red on the fixed arm is the shim's, not the
 * code's. The mechanism the README documents is *device* contention — an fsync
 * on tmpfs has no backing device to wait for and does not block — so this mode
 * models the mechanism instead of modelling "every fsync is slow": an fd whose
 * filesystem reports TMPFS_MAGIC is passed straight through.
 *
 * Two properties of that pass-through are deliberate:
 *   * it does NOT consume the one-shot latch, so a later fsync on a real disk
 *     still gets the full stall. A pass-through that spent the latch would turn
 *     "the store moved to tmpfs" into "the shim ran out of ammunition", which
 *     is a green that means nothing.
 *   * a *failing* fstatfs() stalls rather than skips. The conservative
 *     direction for a reproducer is to fire: a shim that quietly stops firing
 *     reports a pass.
 * The stall line prints the fd's fs magic, so which filesystem was stalled is
 * readable from the run rather than inferred.
 *
 * Build:  gcc -shared -fPIC -o slowfsync.so slowfsync.c -ldl
 * Use:    LD_PRELOAD=/abs/path/slowfsync.so pytest ...
 *         SLOWFSYNC_SKIP_TMPFS=1 LD_PRELOAD=... pytest ...
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <errno.h>
#include <sys/vfs.h>

/* linux/magic.h is not guaranteed on every toolchain this may be built with,
 * and the value is a stable part of the kernel ABI. */
#ifndef TMPFS_MAGIC
#define TMPFS_MAGIC 0x01021994
#endif

/* The server under test is multi-threaded, so the latch is atomic: a plain int
 * is a C11 data race and could spend a second 65s stall on another thread. */
static volatile int stalled = 0;

#define STALL_SECONDS 65

/* The fd's filesystem magic, or 0 when it could not be read. 0 is NOT tmpfs,
 * so an unreadable fd stalls — see the header comment. */
static unsigned long fs_magic(int fd) {
    struct statfs sb;
    if (fstatfs(fd, &sb) != 0) {
        return 0UL;
    }
    return (unsigned long)sb.f_type;
}

int fsync(int fd) {
    static int (*real)(int) = NULL;
    if (!real) {
        *(void **)(&real) = dlsym(RTLD_NEXT, "fsync");
        if (!real) {
            /* Unreachable under LD_PRELOAD (RTLD_NEXT always finds libc's
             * fsync), but a dlopen'd misuse should fail loudly, not SIGSEGV.
             * 🔴 dlerror() CLEARS the error on read, so it must be called ONCE
             * and stashed: `dlerror() ? dlerror() : ...` prints "(null)" and is
             * formally UB — it loses the very reason this branch exists. */
            const char *why = dlerror();
            fprintf(stderr, "[slowfsync] FATAL: dlsym(RTLD_NEXT, \"fsync\") failed: %s\n",
                    why ? why : "(no error reported)");
            errno = ENOSYS;
            return -1;
        }
    }
    unsigned long magic = fs_magic(fd);
    if (getenv("SLOWFSYNC_SKIP_TMPFS") != NULL && magic == TMPFS_MAGIC) {
        /* Deliberately BEFORE the latch, and deliberately loud: a silent skip
         * is indistinguishable from a shim that never attached. */
        fprintf(stderr, "[slowfsync] pass-through fsync(%d): tmpfs (magic=0x%lx), "
                "latch untouched, pid=%d\n", fd, magic, (int)getpid());
        fflush(stderr);
        return real(fd);
    }
    if (!__atomic_test_and_set(&stalled, __ATOMIC_SEQ_CST)) {
        struct timespec t0, t1, rem;
        fprintf(stderr, "[slowfsync] stalling fsync(%d) for %ds (HANG_TIMEOUT=60), "
                "fs magic=0x%lx, pid=%d\n",
                fd, STALL_SECONDS, magic, (int)getpid());
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
