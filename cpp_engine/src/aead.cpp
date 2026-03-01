/**
 * aead.cpp — ChaCha20-Poly1305 AEAD implementation via OpenSSL EVP.
 *
 * Matches the Python cryptography library's ChaCha20Poly1305 behaviour byte
 * for byte:
 *   - AAD = b"FIshare"
 *   - Nonce = 12-byte big-endian of an integer counter
 *   - Tag appended to ciphertext in output
 */

#include "aead.hpp"

#include <openssl/evp.h>
#include <openssl/err.h>

#include <cstring>
#include <stdexcept>
#include <string>

// ── Internal helpers ──────────────────────────────────

/** Pull the first OpenSSL error string off the queue. */
static std::string openssl_error_string() {
    unsigned long err = ERR_get_error();
    if (err == 0) return "unknown OpenSSL error";
    char buf[256];
    ERR_error_string_n(err, buf, sizeof(buf));
    return std::string(buf);
}

/** RAII wrapper for EVP_CIPHER_CTX. */
struct EvpCtx {
    EVP_CIPHER_CTX* p;
    explicit EvpCtx() : p(EVP_CIPHER_CTX_new()) {
        if (!p) throw std::runtime_error("EVP_CIPHER_CTX_new failed: " + openssl_error_string());
    }
    ~EvpCtx() { EVP_CIPHER_CTX_free(p); }
    EvpCtx(const EvpCtx&) = delete;
    EvpCtx& operator=(const EvpCtx&) = delete;
};

// ── Public API ────────────────────────────────────────

void nonce_from_counter(uint64_t counter, uint8_t nonce[AEAD_NONCE_LEN]) {
    // 12 bytes big-endian; first 4 bytes are zero, last 8 encode the counter.
    // Matches Python: n.to_bytes(12, "big")
    std::memset(nonce, 0, AEAD_NONCE_LEN);
    for (int i = 7; i >= 0; --i) {
        nonce[AEAD_NONCE_LEN - 1 - (7 - i)] = static_cast<uint8_t>(counter & 0xFF);
        counter >>= 8;
    }
}

void aead_encrypt(const uint8_t  key[AEAD_KEY_LEN],
                  uint64_t&      nonce_ctr,
                  const uint8_t* plain,
                  size_t         plain_len,
                  uint8_t*       out)
{
    uint8_t nonce[AEAD_NONCE_LEN];
    nonce_from_counter(nonce_ctr, nonce);

    EvpCtx ctx;

    if (EVP_EncryptInit_ex(ctx.p, EVP_chacha20_poly1305(), nullptr, nullptr, nullptr) != 1)
        throw std::runtime_error("EncryptInit cipher: " + openssl_error_string());

    // Set IV length to 12 (nonce length)
    if (EVP_CIPHER_CTX_ctrl(ctx.p, EVP_CTRL_AEAD_SET_IVLEN, AEAD_NONCE_LEN, nullptr) != 1)
        throw std::runtime_error("EncryptInit IVLEN: " + openssl_error_string());

    if (EVP_EncryptInit_ex(ctx.p, nullptr, nullptr, key, nonce) != 1)
        throw std::runtime_error("EncryptInit key+nonce: " + openssl_error_string());

    // Feed AAD — output buffer must be nullptr for AEAD additional data
    int tmp_len = 0;
    if (EVP_EncryptUpdate(ctx.p, nullptr, &tmp_len,
                          AEAD_AAD, static_cast<int>(AEAD_AAD_LEN)) != 1)
        throw std::runtime_error("EncryptUpdate AAD: " + openssl_error_string());

    // Encrypt plaintext
    int out_len = 0;
    if (EVP_EncryptUpdate(ctx.p, out, &out_len,
                          plain, static_cast<int>(plain_len)) != 1)
        throw std::runtime_error("EncryptUpdate data: " + openssl_error_string());

    // Finalise (produces 0 extra bytes for stream ciphers, but required for tag)
    int final_len = 0;
    if (EVP_EncryptFinal_ex(ctx.p, out + out_len, &final_len) != 1)
        throw std::runtime_error("EncryptFinal: " + openssl_error_string());

    // Append 16-byte Poly1305 tag after ciphertext
    if (EVP_CIPHER_CTX_ctrl(ctx.p, EVP_CTRL_AEAD_GET_TAG, AEAD_TAG_LEN,
                             out + out_len + final_len) != 1)
        throw std::runtime_error("GET_TAG: " + openssl_error_string());

    nonce_ctr++;   // advance counter after successful encrypt
}

void aead_decrypt(const uint8_t  key[AEAD_KEY_LEN],
                  uint64_t&      nonce_ctr,
                  const uint8_t* cipher_and_tag,
                  size_t         ct_len,
                  uint8_t*       out)
{
    if (ct_len < AEAD_TAG_LEN)
        throw std::runtime_error("aead_decrypt: ciphertext too short");

    size_t cipher_len = ct_len - AEAD_TAG_LEN;
    const uint8_t* tag_ptr = cipher_and_tag + cipher_len;

    uint8_t nonce[AEAD_NONCE_LEN];
    nonce_from_counter(nonce_ctr, nonce);

    EvpCtx ctx;

    if (EVP_DecryptInit_ex(ctx.p, EVP_chacha20_poly1305(), nullptr, nullptr, nullptr) != 1)
        throw std::runtime_error("DecryptInit cipher: " + openssl_error_string());

    if (EVP_CIPHER_CTX_ctrl(ctx.p, EVP_CTRL_AEAD_SET_IVLEN, AEAD_NONCE_LEN, nullptr) != 1)
        throw std::runtime_error("DecryptInit IVLEN: " + openssl_error_string());

    if (EVP_DecryptInit_ex(ctx.p, nullptr, nullptr, key, nonce) != 1)
        throw std::runtime_error("DecryptInit key+nonce: " + openssl_error_string());

    // Feed AAD
    int tmp_len = 0;
    if (EVP_DecryptUpdate(ctx.p, nullptr, &tmp_len,
                          AEAD_AAD, static_cast<int>(AEAD_AAD_LEN)) != 1)
        throw std::runtime_error("DecryptUpdate AAD: " + openssl_error_string());

    // Decrypt ciphertext
    int out_len = 0;
    if (EVP_DecryptUpdate(ctx.p, out, &out_len,
                          cipher_and_tag, static_cast<int>(cipher_len)) != 1)
        throw std::runtime_error("DecryptUpdate data: " + openssl_error_string());

    // Set expected tag BEFORE calling DecryptFinal
    // EVP_CTRL_AEAD_SET_TAG requires a non-const pointer; cast away const (OpenSSL contract)
    if (EVP_CIPHER_CTX_ctrl(ctx.p, EVP_CTRL_AEAD_SET_TAG, AEAD_TAG_LEN,
                             const_cast<uint8_t*>(tag_ptr)) != 1)
        throw std::runtime_error("SET_TAG: " + openssl_error_string());

    // Finalise — returns <= 0 if tag verification fails
    int final_len = 0;
    if (EVP_DecryptFinal_ex(ctx.p, out + out_len, &final_len) <= 0)
        throw std::runtime_error("AEAD authentication failed (tag mismatch)");

    nonce_ctr++;   // advance counter after successful decrypt
}
