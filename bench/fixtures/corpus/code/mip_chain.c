/* mip level helpers, toy example code */
#include <stddef.h>

int mip_count(int w, int h)
{
    int n = 1;
    while (w > 1 || h > 1) {
        w = w > 1 ? w / 2 : 1;
        h = h > 1 ? h / 2 : 1;
        n++;
    }
    return n;
}

void box_filter_row(const unsigned char *src, unsigned char *dst, int dst_w, int src_stride)
{
    /* average blocks of 2 by 2 pixels, one output row */
    for (int x = 0; x < dst_w; x++) {
        int a = src[2 * x];
        int b = src[2 * x + 1];
        int c = src[src_stride + 2 * x];
        int d = src[src_stride + 2 * x + 1];
        dst[x] = (unsigned char)((a + b + c + d + 2) / 4);
    }
}

void build_mips(unsigned char *base, int w, int h, unsigned char **levels)
{
    /* levels[i] points to the output buffer of level i + 1 */
    int n = mip_count(w, h);
    const unsigned char *cur = base;
    for (int i = 1; i < n; i++) {
        int lw = w >> i;
        int lh = h >> i;
        for (int y = 0; y < lh; y++) {
            box_filter_row(cur + 2 * y * (lw * 2), levels[i - 1] + y * lw, lw, lw * 2);
        }
        cur = levels[i - 1];
    }
}
