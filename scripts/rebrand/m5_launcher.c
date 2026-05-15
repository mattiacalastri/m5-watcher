// M5 Watcher launcher (sess.1988) — Mach-O entry point for M5 Watcher.app
// exec's ./ghostty.bin (sibling) with fixed args. Bash wrapper was rejected
// by Launchd POSIX 162 — main executable must be Mach-O.
#include <unistd.h>
#include <libgen.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char exe_path[4096];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        fprintf(stderr, "M5 Watcher launcher: _NSGetExecutablePath failed\n");
        return 1;
    }

    // Resolve dirname (Contents/MacOS) and build path to sibling ghostty.bin
    char *dir = dirname(exe_path);
    char bin_path[4096];
    snprintf(bin_path, sizeof(bin_path), "%s/ghostty.bin", dir);

    // Args passed to ghostty: title, fullscreen, class, sizing.
    // sess.1988: --command (trusted config key, no exec prompt) invece di -e
    // (-e triggera Ghostty security dialog + apre window separata).
    char *new_argv[] = {
        bin_path,
        "--title=M5 Watcher",
        "--fullscreen=true",
        "--class=com.polpo.m5-watcher",
        "--window-width=180",
        "--window-height=50",
        "--command=/Users/mattiacalastri/projects/m5-watcher/run.sh",
        NULL
    };

    execv(bin_path, new_argv);
    // execv only returns on failure
    perror("M5 Watcher launcher: execv failed");
    return 1;
}
