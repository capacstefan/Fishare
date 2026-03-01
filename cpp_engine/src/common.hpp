#pragma once
/**
 * common.hpp — Platform detection, socket compatibility, and utilities.
 *
 * On Windows, runtime sockets are SOCKET (UINT_PTR). On POSIX they are plain
 * int file descriptors. This header unifies them behind sock_t and provides a
 * portable send_all() that retries partial sends the same way Python's
 * sock.sendall() does.
 */

#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

// ── Platform-specific socket setup ────────────────────

#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <winsock2.h>
#  include <ws2tcpip.h>
   using sock_t = SOCKET;
   static constexpr sock_t INVALID_SOCK = INVALID_SOCKET;
#  pragma comment(lib, "ws2_32.lib")
#else
#  include <sys/socket.h>
#  include <unistd.h>
#  include <fcntl.h>
   using sock_t = int;
   static constexpr sock_t INVALID_SOCK = -1;
#endif

// ── Error helpers ──────────────────────────────────────

inline std::string last_socket_error() {
#ifdef _WIN32
    int code = WSAGetLastError();
    char buf[256] = {};
    FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                   nullptr, code, 0, buf, sizeof(buf), nullptr);
    return std::string(buf) + " (code " + std::to_string(code) + ")";
#else
    return std::string(std::strerror(errno));
#endif
}

// Python passes socket.fileno() as a Python int — which fits in int64_t.
// Cast carefully: on Windows SOCKET is UINT_PTR (potentially > INT32_MAX).
inline sock_t to_sock(int64_t fd) {
    return static_cast<sock_t>(static_cast<uintptr_t>(fd));
}

// ── Portable send_all (mirrors Python sock.sendall) ──

inline void send_all(sock_t sock, const uint8_t* data, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        auto n = ::send(sock,
                        reinterpret_cast<const char*>(data + sent),
                        static_cast<int>(len - sent),
                        0);
        if (n <= 0) {
            throw std::runtime_error("send() failed: " + last_socket_error());
        }
        sent += static_cast<size_t>(n);
    }
}

// ── Portable recv_exact (mirrors Python _recv_exact) ─

inline void recv_exact(sock_t sock, uint8_t* buf, size_t n) {
    size_t got = 0;
    while (got < n) {
        auto r = ::recv(sock,
                        reinterpret_cast<char*>(buf + got),
                        static_cast<int>(n - got),
                        0);
        if (r <= 0) {
            if (r == 0)
                throw std::runtime_error("Connection closed by peer");
            throw std::runtime_error("recv() failed: " + last_socket_error());
        }
        got += static_cast<size_t>(r);
    }
}

// ── Big-endian 32-bit helpers ─────────────────────────

inline void write_be32(uint8_t* dst, uint32_t v) {
    dst[0] = static_cast<uint8_t>((v >> 24) & 0xFF);
    dst[1] = static_cast<uint8_t>((v >> 16) & 0xFF);
    dst[2] = static_cast<uint8_t>((v >>  8) & 0xFF);
    dst[3] = static_cast<uint8_t>( v        & 0xFF);
}

inline uint32_t read_be32(const uint8_t* src) {
    return (static_cast<uint32_t>(src[0]) << 24) |
           (static_cast<uint32_t>(src[1]) << 16) |
           (static_cast<uint32_t>(src[2]) <<  8) |
            static_cast<uint32_t>(src[3]);
}
