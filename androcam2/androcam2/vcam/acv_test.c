/* Test harness: acv_test <shm file> <dw> <dh> <bottom_up> <out file>  (prints "live" or "idle") */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include "shm_layout.h"

int main(int argc, char **argv) {
    if (argc != 6) return 2;
    int fd = open(argv[1], O_RDONLY);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) != 0) return 3;
    const uint8_t *base = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) return 4;
    int dw = atoi(argv[2]), dh = atoi(argv[3]), bu = atoi(argv[4]);
    uint8_t *dst = malloc((size_t)dw * dh * 3);
    int live = acv_render(base, (size_t)st.st_size, dst, dw, dh, bu);
    FILE *f = fopen(argv[5], "wb");
    fwrite(dst, 1, (size_t)dw * dh * 3, f);
    fclose(f);
    puts(live ? "live" : "idle");
    return 0;
}
