/**
 * engine.cpp — FIshare C++ transfer engine (simplified & consolidated).
 *
 * High-performance file transfer with zero-copy I/O and ChaCha20-Poly1305 AEAD.
 * All blocking operations release the GIL for true Python parallelism.
 * 
 * This consolidated version uses aead.hpp for encryption instead of duplicating code.
 */

#include "engine.hpp"
#include "aead.hpp"

#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _WIN32
#  include <windows.h>
#endif

// ── Constants ─────────────────────────────────────────

static constexpr size_t  MAX_FRAME_SIZE    = 100u << 20;   // 100 MB
static constexpr int64_t PROGRESS_INTERVAL = 100;           // ms

// ── Socket I/O helpers ────────────────────────────────

static void send_all(sock_t sock, const uint8_t* data, size_t len) {
    while (len > 0) {
        int sent = ::send(sock, reinterpret_cast<const char*>(data), static_cast<int>(len), 0);
        if (sent <= 0) throw std::runtime_error("send() failed");
        data += sent;
        len -= static_cast<size_t>(sent);
    }
}

static void recv_exact(sock_t sock, uint8_t* data, size_t len) {
    while (len > 0) {
        int received = ::recv(sock, reinterpret_cast<char*>(data), static_cast<int>(len), 0);
        if (received == 0) throw std::runtime_error("Connection closed by peer");
        if (received < 0) throw std::runtime_error("recv() failed");
        data += received;
        len -= static_cast<size_t>(received);
    }
}

// ── Endianness helpers ────────────────────────────────

static void write_be32(uint8_t* p, uint32_t v) {
    p[0] = (v >> 24) & 0xFF;
    p[1] = (v >> 16) & 0xFF;
    p[2] = (v >> 8) & 0xFF;
    p[3] = v & 0xFF;
}

static uint32_t read_be32(const uint8_t* p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}

// ── Framing helpers ([4-byte length][encrypted payload]) ──

static void send_frame(sock_t sock, const uint8_t* data, size_t len,
                       const uint8_t key[AEAD_KEY_LEN], uint64_t& nonce,
                       std::vector<uint8_t>& frame_buf)
{
    // Single allocation: [4-byte header][ciphertext+tag]
    size_t payload_len = len + AEAD_TAG_LEN;
    frame_buf.resize(4 + payload_len);
    
    // Encrypt directly into frame buffer
    aead_encrypt(key, nonce, data, len, frame_buf.data() + 4);
    
    // Write big-endian length header
    write_be32(frame_buf.data(), static_cast<uint32_t>(payload_len));
    
    // Single send call (no Nagle fragmentation)
    send_all(sock, frame_buf.data(), frame_buf.size());
}

static void recv_frame(sock_t sock,
                       const uint8_t key[AEAD_KEY_LEN], uint64_t& nonce,
                       std::vector<uint8_t>& cipher_buf,
                       std::vector<uint8_t>& plain_buf)
{
    // Read 4-byte length header
    uint8_t header[4];
    recv_exact(sock, header, 4);
    uint32_t payload_len = read_be32(header);
    
    if (payload_len > MAX_FRAME_SIZE)
        throw std::runtime_error("Frame too large: " + std::to_string(payload_len));
    if (payload_len < AEAD_TAG_LEN)
        throw std::runtime_error("Frame too small");
    
    // Reuse caller-supplied buffers — no heap allocation when capacity >= payload_len
    cipher_buf.resize(payload_len);
    recv_exact(sock, cipher_buf.data(), payload_len);
    
    // Decrypt into reused output buffer
    plain_buf.resize(payload_len - AEAD_TAG_LEN);
    aead_decrypt(key, nonce, cipher_buf.data(), payload_len, plain_buf.data());
}

// ── File helpers (UTF-8 paths, cross-platform) ────────

static FILE* file_open(const std::string& path, const char* mode) {
#ifdef _WIN32
    int wn = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
    if (wn <= 0) return nullptr;
    std::wstring wpath(static_cast<size_t>(wn), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wpath.data(), wn);
    
    std::wstring wmode(mode, mode + std::strlen(mode));
    FILE* f = nullptr;
    _wfopen_s(&f, wpath.c_str(), wmode.c_str());
    return f;
#else
    return std::fopen(path.c_str(), mode);
#endif
}

static void fremove(const std::string& path) {
#ifdef _WIN32
    int wn = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
    if (wn > 0) {
        std::wstring wpath(static_cast<size_t>(wn), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wpath.data(), wn);
        DeleteFileW(wpath.c_str());
    }
#else
    std::remove(path.c_str());
#endif
}

// ═══════════════════════════════════════════════════════
//  Public API: File Transfer Functions
// ═══════════════════════════════════════════════════════

uint64_t eng_send_file(
    sock_t sock,
    const std::string& file_path,
    int64_t fsize,
    const uint8_t key[KEY_LEN],
    uint64_t nonce,
    const std::function<void(int64_t, int64_t)>& progress_cb,
    int64_t bytes_offset,
    int64_t grand_total,
    int chunk_size
) {
    if (fsize == 0) return nonce;
    
    FILE* f = file_open(file_path, "rb");
    if (!f)
        throw std::runtime_error("Cannot open file: " + file_path);
    
    // Pre-allocate buffers (reused for all chunks - zero extra allocs)
    std::vector<uint8_t> read_buf(static_cast<size_t>(chunk_size));
    std::vector<uint8_t> frame_buf;
    frame_buf.reserve(4 + static_cast<size_t>(chunk_size) + AEAD_TAG_LEN);
    
    int64_t sent = 0;
    auto last_progress = std::chrono::steady_clock::now();
    
    try {
        while (sent < fsize) {
            int want = static_cast<int>(std::min<int64_t>(chunk_size, fsize - sent));
            size_t got = std::fread(read_buf.data(), 1, static_cast<size_t>(want), f);
            
            if (got == 0) {
                bool is_err = std::ferror(f) != 0;
                throw std::runtime_error(
                    std::string(is_err ? "Read error: " : "Unexpected EOF: ") + file_path
                );
            }
            
            send_frame(sock, read_buf.data(), got, key, nonce, frame_buf);
            sent += static_cast<int64_t>(got);
            
            // Throttled progress callback (max 10/sec)
            if (progress_cb && grand_total > 0) {
                auto now = std::chrono::steady_clock::now();
                auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - last_progress
                ).count();
                if (ms >= PROGRESS_INTERVAL) {
                    progress_cb(bytes_offset + sent, grand_total);
                    last_progress = now;
                }
            }
        }
    } catch (...) {
        std::fclose(f);
        throw;
    }
    
    std::fclose(f);
    return nonce;
}

uint64_t eng_recv_file(
    sock_t sock,
    const std::string& dest_path,
    int64_t fsize,
    const uint8_t key[KEY_LEN],
    uint64_t nonce,
    const std::function<void(int64_t, int64_t)>& progress_cb,
    int64_t bytes_offset,
    int64_t grand_total
) {
    if (fsize == 0) {
        // Create empty file
        FILE* f = file_open(dest_path, "wb");
        if (f) std::fclose(f);
        return nonce;
    }
    
    FILE* f = file_open(dest_path, "wb");
    if (!f)
        throw std::runtime_error("Cannot create file: " + dest_path);
    
    int64_t received = 0;
    auto last_progress = std::chrono::steady_clock::now();
    
    // Pre-allocate recv buffers for the expected frame size once, reused every iteration.
    size_t expected_chunk = static_cast<size_t>(std::min<int64_t>(fsize, 16 * 1024 * 1024));
    std::vector<uint8_t> cipher_buf, plain_buf;
    cipher_buf.reserve(expected_chunk + AEAD_TAG_LEN);
    plain_buf.reserve(expected_chunk);
    
    try {
        while (received < fsize) {
            recv_frame(sock, key, nonce, cipher_buf, plain_buf);
            
            if (plain_buf.empty())
                throw std::runtime_error("Received empty chunk");
            
            size_t written = std::fwrite(plain_buf.data(), 1, plain_buf.size(), f);
            if (written != plain_buf.size())
                throw std::runtime_error("Write error: " + dest_path);
            
            received += static_cast<int64_t>(plain_buf.size());
            
            // Throttled progress callback
            if (progress_cb && grand_total > 0) {
                auto now = std::chrono::steady_clock::now();
                auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - last_progress
                ).count();
                if (ms >= PROGRESS_INTERVAL) {
                    progress_cb(bytes_offset + received, grand_total);
                    last_progress = now;
                }
            }
        }
    } catch (...) {
        std::fclose(f);
        fremove(dest_path);  // Cleanup partial file on error
        throw;
    }
    
    std::fclose(f);
    return nonce;
}
