#pragma once
/**
 * engine.hpp — FIshare C++ transfer engine (public interface).
 *
 * Two functions: send_file and recv_file.
 * Everything else (AEAD, framing, socket I/O) lives in engine.cpp.
 *
 * Nonce contract: pass the current Python aead.send_nonce / aead.recv_nonce
 * in; the function returns the new value after all frames are processed.
 * Write it back to aead.send_nonce / aead.recv_nonce to stay in sync.
 *
 * All blocking I/O runs without the Python GIL (released by bindings.cpp).
 */

#include <cstdint>
#include <functional>
#include <string>

// Platform socket handle (SOCKET on Windows, int on POSIX).
#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <winsock2.h>
   using sock_t = SOCKET;
#else
   using sock_t = int;
#endif

static constexpr size_t KEY_LEN = 32;

/**
 * Send fsize bytes from file_path over sock using the AEAD-framed protocol.
 * Returns the nonce value after the last frame (= start nonce + frames sent).
 * Raises std::runtime_error on any I/O or crypto failure.
 */
uint64_t eng_send_file(sock_t             sock,
                       const std::string& file_path,
                       int64_t            fsize,
                       const uint8_t      key[KEY_LEN],
                       uint64_t           nonce,
                       const std::function<void(int64_t, int64_t)>& progress_cb,
                       int64_t            bytes_offset,
                       int64_t            grand_total,
                       int                chunk_size);

/**
 * Receive fsize bytes from sock and write them to dest_path.
 * Returns the nonce value after the last frame.
 * Deletes the partial file before raising on any error.
 */
uint64_t eng_recv_file(sock_t             sock,
                       const std::string& dest_path,
                       int64_t            fsize,
                       const uint8_t      key[KEY_LEN],
                       uint64_t           nonce,
                       const std::function<void(int64_t, int64_t)>& progress_cb,
                       int64_t            bytes_offset,
                       int64_t            grand_total);
