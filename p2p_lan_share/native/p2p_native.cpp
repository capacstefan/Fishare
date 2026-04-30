// p2p_native.cpp — minimal SHA-256 streaming hasher exported via C ABI.
//
// Compiled as p2p_native.dll, loaded by p2p_lan_share.native via ctypes.
// ctypes.CDLL releases the Python GIL for every call into here, so the
// per-chunk hashing in the file-transfer hot loop runs without blocking
// other Python threads (network I/O, GUI, sync).
//
// No external dependencies, no STL containers; deliberately tiny.
//
// Build (VS 2022 — "x64 Native Tools Command Prompt"):
//     cl /LD /O2 /EHsc /nologo /utf-8 p2p_native.cpp /Fe:p2p_native.dll
// or just run native/build.py.

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#  define P2P_API extern "C" __declspec(dllexport)
#else
#  define P2P_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr uint32_t K[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
};

inline uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

struct Sha256 {
    uint32_t h[8];
    uint64_t total_bits;
    uint8_t  buf[64];
    size_t   buf_len;
};

void sha256_init(Sha256* c) {
    static const uint32_t H0[8] = {
        0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
        0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19,
    };
    memcpy(c->h, H0, sizeof(H0));
    c->total_bits = 0;
    c->buf_len = 0;
}

void sha256_compress(Sha256* c, const uint8_t* p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = (uint32_t(p[i*4])<<24) | (uint32_t(p[i*4+1])<<16)
             | (uint32_t(p[i*4+2])<<8) | uint32_t(p[i*4+3]);
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i-15],  7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
        uint32_t s1 = rotr(w[i-2],  17) ^ rotr(w[i-2],  19) ^ (w[i-2]  >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=c->h[0], b=c->h[1], cv=c->h[2], d=c->h[3];
    uint32_t e=c->h[4], f=c->h[5], g=c->h[6],  hh=c->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = hh + S1 + ch + K[i] + w[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t mj = (a & b) ^ (a & cv) ^ (b & cv);
        uint32_t t2 = S0 + mj;
        hh = g; g = f; f = e; e = d + t1; d = cv; cv = b; b = a; a = t1 + t2;
    }
    c->h[0]+=a; c->h[1]+=b; c->h[2]+=cv; c->h[3]+=d;
    c->h[4]+=e; c->h[5]+=f; c->h[6]+=g;  c->h[7]+=hh;
}

void sha256_update(Sha256* c, const uint8_t* data, size_t len) {
    c->total_bits += uint64_t(len) * 8;
    if (c->buf_len) {
        size_t take = 64 - c->buf_len;
        if (take > len) take = len;
        memcpy(c->buf + c->buf_len, data, take);
        c->buf_len += take;
        data += take; len -= take;
        if (c->buf_len == 64) { sha256_compress(c, c->buf); c->buf_len = 0; }
    }
    while (len >= 64) { sha256_compress(c, data); data += 64; len -= 64; }
    if (len) { memcpy(c->buf, data, len); c->buf_len = len; }
}

void sha256_final(Sha256* c, uint8_t out[32]) {
    uint64_t bits = c->total_bits;
    c->buf[c->buf_len++] = 0x80;
    if (c->buf_len > 56) {
        while (c->buf_len < 64) c->buf[c->buf_len++] = 0;
        sha256_compress(c, c->buf);
        c->buf_len = 0;
    }
    while (c->buf_len < 56) c->buf[c->buf_len++] = 0;
    for (int i = 7; i >= 0; i--) c->buf[c->buf_len++] = uint8_t(bits >> (i * 8));
    sha256_compress(c, c->buf);
    for (int i = 0; i < 8; i++) {
        out[i*4    ] = uint8_t(c->h[i] >> 24);
        out[i*4 + 1] = uint8_t(c->h[i] >> 16);
        out[i*4 + 2] = uint8_t(c->h[i] >>  8);
        out[i*4 + 3] = uint8_t(c->h[i]      );
    }
}

} // namespace


// ---- Public C ABI ---------------------------------------------------------
P2P_API void* p2p_sha256_new(void) {
    Sha256* c = (Sha256*)malloc(sizeof(Sha256));
    if (c) sha256_init(c);
    return c;
}

P2P_API void p2p_sha256_update(void* h, const uint8_t* data, size_t len) {
    if (h && data && len) sha256_update((Sha256*)h, data, len);
}

P2P_API void p2p_sha256_final(void* h, uint8_t out[32]) {
    if (h && out) sha256_final((Sha256*)h, out);
}

P2P_API void p2p_sha256_free(void* h) { free(h); }

// Tiny "did we load OK?" probe used by the Python wrapper.
P2P_API uint32_t p2p_native_version(void) { return 0x00010000; /* 1.0.0 */ }
