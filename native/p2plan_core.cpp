// =============================================================================
// p2plan_core - native TLS file pump for p2p_lan_share.
//
// Purpose (KISS):
//   Two functions, send_file() and recv_file(). Each is a tight loop that
//   moves bytes between a file on disk and a Python ssl.SSLSocket /
//   LineReader.
//
//   The loop lives in C++ so that:
//     * no Python bytecode runs per chunk,
//     * one reusable heap buffer is used for sends (no per-chunk allocations),
//     * fread/fwrite happen with the GIL released, letting the Qt UI thread
//       continue painting even during a large transfer,
//     * the progress callback is throttled here (fixed ~200 ms cadence),
//       which is what the UI actually needs.
//
// What it is NOT:
//   * It does NOT talk to OpenSSL directly. TLS stays inside Python's ssl
//     module. We just call sslsock.send() and reader.recv() from C++. This
//     keeps the code portable across CPython versions and avoids bundling
//     OpenSSL ourselves.
//   * It does not implement the transfer protocol. Python still reads the
//     offer, writes the accept/reject response, and decides when to call
//     send_file / recv_file.
//
// Flow (send side):
//   [disk] -- fread --> [C++ buffer] -- memoryview --> sslsock.send() --> TLS
//
// Flow (recv side):
//   TLS -- reader.recv(n) --> [bytes] -- fwrite --> [disk]
//
//   (recv uses Python's LineReader instead of sslsock.recv directly because
//    LineReader already holds any bytes that were pre-buffered while reading
//    the offer JSON line.)
// =============================================================================

#include <pybind11/pybind11.h>
#include <pybind11/functional.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

namespace py = pybind11;

// ---- Tunables ---------------------------------------------------------------
// 1 MiB matches config.CHUNK on the Python side. Keep them equal.
static constexpr size_t CHUNK = 1u << 20;

// How often to fire the progress callback. Roughly 5 Hz is what the UI needs.
static constexpr auto PROGRESS_INTERVAL = std::chrono::milliseconds(200);

// -----------------------------------------------------------------------------
// send_file: stream `path` over `sslsock`. Returns total bytes sent.
//
// Arguments:
//   sslsock     - a Python ssl.SSLSocket (or anything with .send(memoryview))
//   path        - filesystem path as UTF-8 string
//   progress_cb - called from C++ every ~200 ms with the running byte count
// -----------------------------------------------------------------------------
static py::int_ send_file(py::object sslsock,
                          const std::string &path,
                          py::function progress_cb)
{
    std::FILE *fp = std::fopen(path.c_str(), "rb");
    if (!fp)
        throw std::runtime_error("cannot open for reading: " + path);

    // Disable the CRT's own tiny stdio buffer. We supply a 1 MiB buffer
    // ourselves; the CRT layer just adds extra system-call overhead.
    std::setvbuf(fp, nullptr, _IONBF, 0);

    // One heap buffer, reused for the whole file. No per-chunk allocation.
    std::vector<char> buf(CHUNK);

    // Use sendall() instead of send().
    //
    // Rationale: ssl.SSLSocket.sendall() drives its retry loop entirely inside
    // Python's C extension (libssl), releasing the GIL for the actual
    // encrypt+write work and handling partial writes without returning to our
    // C++ loop. Calling send() from C++ instead means:
    //   * one new py::memoryview object per partial send (heap alloc + refcount),
    //   * one GIL acquire/release cycle per call to send(),
    //   * our outer while(off<n) loop running in interpreted C++ bridging code
    //     rather than in Python's optimised ssl C layer.
    // Net result: MORE GIL churn, not less — hence the regression.
    py::object py_sendall = sslsock.attr("sendall");

    size_t total_sent = 0;
    auto last_tick = std::chrono::steady_clock::now();

    try
    {
        while (true)
        {
            size_t n;
            {
                // Disk read doesn't touch Python - release the GIL so the
                // main thread can keep rendering the UI.
                py::gil_scoped_release unlock;
                n = std::fread(buf.data(), 1, CHUNK, fp);
            }
            if (n == 0)
                break; // EOF.

            // One Python bytes object per chunk, one sendall call.
            // ssl.sendall() handles partial writes and GIL release internally.
            py::bytes chunk(buf.data(), static_cast<py::ssize_t>(n));
            py_sendall(chunk);
            total_sent += n;

            auto now = std::chrono::steady_clock::now();
            if (now - last_tick >= PROGRESS_INTERVAL)
            {
                progress_cb(total_sent);
                last_tick = now;
            }
        }
    }
    catch (...)
    {
        std::fclose(fp);
        throw;
    }

    std::fclose(fp);
    // One final tick so the UI can paint exactly 100 %.
    progress_cb(total_sent);
    return py::int_(total_sent);
}

// -----------------------------------------------------------------------------
// recv_file: read exactly `total_size` bytes from `reader`, write to `path`.
// Returns total bytes received (== total_size on success).
//
// `reader` is a duck-typed object exposing `recv(n) -> bytes`. Python's
// LineReader satisfies this and transparently drains any bytes it had
// already buffered while reading the offer JSON.
// -----------------------------------------------------------------------------
static py::int_ recv_file(py::object reader,
                          const std::string &path,
                          py::int_ total_size,
                          py::function progress_cb)
{
    std::FILE *fp = std::fopen(path.c_str(), "wb");
    if (!fp)
        throw std::runtime_error("cannot open for writing: " + path);

    // Same reasoning as send_file: disable CRT buffering so fwrite goes
    // straight to the OS page cache without a redundant intermediate copy.
    std::setvbuf(fp, nullptr, _IONBF, 0);

    const size_t total = total_size.cast<size_t>();
    size_t received = 0;
    auto last_tick = std::chrono::steady_clock::now();

    py::object py_recv = reader.attr("recv");

    try
    {
        while (received < total)
        {
            size_t want = std::min(CHUNK, total - received);
            py::bytes chunk = py_recv(py::int_(want));

            // Zero-copy view into the bytes object just returned by Python.
            char *data = nullptr;
            py::ssize_t n = 0;
            if (PYBIND11_BYTES_AS_STRING_AND_SIZE(chunk.ptr(), &data, &n) != 0)
                throw std::runtime_error("recv returned non-bytes");
            if (n == 0)
                throw std::runtime_error("connection lost during file receive");

            received += static_cast<size_t>(n);

            {
                // fwrite doesn't touch Python - release the GIL.
                py::gil_scoped_release unlock;
                std::fwrite(data, 1, static_cast<size_t>(n), fp);
            }

            auto now = std::chrono::steady_clock::now();
            if (now - last_tick >= PROGRESS_INTERVAL)
            {
                progress_cb(received);
                last_tick = now;
            }
        }
    }
    catch (...)
    {
        std::fclose(fp);
        throw;
    }

    std::fclose(fp);
    progress_cb(received);
    return py::int_(received);
}

// -----------------------------------------------------------------------------
// Module registration. Two functions, no classes, no state.
// -----------------------------------------------------------------------------
PYBIND11_MODULE(p2plan_core, m)
{
    m.doc() = "Native TLS file pump for p2p_lan_share (pybind11).";
    m.def("send_file", &send_file,
          py::arg("sslsock"), py::arg("path"), py::arg("progress_cb"),
          "Stream a file to an ssl.SSLSocket. Returns bytes sent.");
    m.def("recv_file", &recv_file,
          py::arg("reader"), py::arg("path"),
          py::arg("total_size"), py::arg("progress_cb"),
          "Receive total_size bytes from a LineReader-like object and "
          "write them to `path`. Returns bytes received.");
}
