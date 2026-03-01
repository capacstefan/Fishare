/**
 * bindings.cpp â€” pybind11 module "cpp_engine".
 *
 * Exposes four functions to Python:
 *
 *   send_frame(sock_fd, data, key, nonce) -> int (next nonce)
 *   recv_frame(sock_fd, key, nonce)       -> tuple[bytes, int]
 *   send_file (sock_fd, path, fsize, key, send_nonce,
 *              progress_cb, bytes_offset, grand_total, chunk_size)
 *                                         -> tuple[int bytes_sent, int next_nonce]
 *   recv_file (sock_fd, path, fsize, key, recv_nonce,
 *              progress_cb, bytes_offset, grand_total)
 *                                         -> tuple[int bytes_recv, int next_nonce]
 *
 * GIL contract
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 *  - These functions are called with the GIL held (normal Python call).
 *  - Argument data (bytes buffers, string paths) is read while GIL is held.
 *  - Before entering blocking I/O, the GIL is released.
 *  - If a progress_cb is supplied, the C++ engine callback lambda
 *    re-acquires the GIL for the duration of the Python call, then releases
 *    it again.  Net effect: Python's GIL is free during almost all I/O.
 *
 * Nonce contract
 * â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
 *  - The caller (transfer_tcp.py) reads aead.send_nonce / aead.recv_nonce
 *    AFTER the JSON control phase and passes them here.
 *  - The C++ engine increments the nonce internally per frame.
 *  - The final nonce value is returned so Python can sync aead.send_nonce /
 *    aead.recv_nonce before the next JSON control message (if any).
 */

#include <pybind11/pybind11.h>
#include <pybind11/functional.h>

#include "engine.hpp"

#include <cstring>
#include <functional>
#include <stdexcept>

namespace py = pybind11;

// â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

static void extract_key(const py::bytes& key_obj, uint8_t out[KEY_LEN]) {
    char*      buf;
    Py_ssize_t length;
    if (PyBytes_AsStringAndSize(key_obj.ptr(), &buf, &length) != 0)
        throw py::error_already_set();
    if (static_cast<size_t>(length) != KEY_LEN)
        throw std::invalid_argument("key must be exactly 32 bytes, got " +
                                    std::to_string(length));
    std::memcpy(out, buf, KEY_LEN);
}

static std::function<void(int64_t, int64_t)>
make_progress_cb(py::object py_cb) {
    if (py_cb.is_none()) return {};
    return [py_cb](int64_t done, int64_t total) {
        py::gil_scoped_acquire acquire;
        py_cb(done, total);
    };
}

#ifdef _WIN32
static sock_t to_sock(int64_t fd) { return reinterpret_cast<SOCKET>(fd); }
#else
static sock_t to_sock(int64_t fd) { return static_cast<int>(fd); }
#endif

// â”€â”€ Module definition â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

PYBIND11_MODULE(cpp_engine, m) {
    m.doc() = "FIshare C++ transfer engine — Simplified & Optimized";

    // send_file(fd, path, fsize, key, nonce, cb, offset, total, chunk) -> uint64_t
    m.def(
        "send_file",
        [](int64_t sock_fd,const std::string& file_path, int64_t fsize,
           py::bytes key, uint64_t send_nonce, py::object progress_cb,
           int64_t bytes_offset, int64_t grand_total, int chunk_size) -> uint64_t
        {
            uint8_t key_buf[KEY_LEN];
            extract_key(key, key_buf);
            if (chunk_size <= 0)
                throw std::invalid_argument("chunk_size must be positive");
            
            sock_t sock = to_sock(sock_fd);
            auto cb = make_progress_cb(std::move(progress_cb));
            
            uint64_t final_nonce;
            {
                py::gil_scoped_release release;  // Release GIL for I/O
                final_nonce = eng_send_file(
                    sock, file_path, fsize, key_buf, send_nonce,
                    cb, bytes_offset, grand_total, chunk_size
                );
            }
            return final_nonce;
        },
        py::arg("sock_fd"), py::arg("file_path"), py::arg("fsize"),
        py::arg("key"), py::arg("send_nonce"), py::arg("progress_cb"),
        py::arg("bytes_offset"), py::arg("grand_total"), py::arg("chunk_size"),
        "Send a file via AEAD-framed pipeline. Returns final send_nonce.");

    // recv_file(fd, dest, fsize, key, nonce, cb, offset, total) -> uint64_t
    m.def(
        "recv_file",
        [](int64_t sock_fd, const std::string& dest_path, int64_t fsize,
           py::bytes key, uint64_t recv_nonce, py::object progress_cb,
           int64_t bytes_offset, int64_t grand_total) -> uint64_t
        {
            uint8_t key_buf[KEY_LEN];
            extract_key(key, key_buf);
            
            sock_t sock = to_sock(sock_fd);
            auto cb = make_progress_cb(std::move(progress_cb));
            
            uint64_t final_nonce;
            {
                py::gil_scoped_release release;  // Release GIL for I/O
                final_nonce = eng_recv_file(
                    sock, dest_path, fsize, key_buf, recv_nonce,
                    cb, bytes_offset, grand_total
                );
            }
            return final_nonce;
        },
        py::arg("sock_fd"), py::arg("dest_path"), py::arg("fsize"),
        py::arg("key"), py::arg("recv_nonce"), py::arg("progress_cb"),
        py::arg("bytes_offset"), py::arg("grand_total"),
        "Receive file via AEAD-framed pipeline. Returns final recv_nonce.");

    m.attr("__version__") = "3.0.0";  // Simplified & consolidated version
    m.attr("available")   = true;
}
