#pragma once
/**
 * aead.hpp — ChaCha20-Poly1305 AEAD interface.
 *
 * Exactly replicates Python's AEADStream behaviour:
 *   - Key: 32 bytes
 *   - Nonce: 12-byte big-endian encoding of a uint64_t counter
 *   - AAD (additional authenticated data): "FIshare" (7 bytes)
 *   - Tag: 16 bytes, appended to ciphertext in the output buffer
 *   - Wire format: ciphertext || tag  (plaintext length == ciphertext length,
 *                  output length == plaintext_len + AEAD_TAG_LEN)
 *
 * The caller is responsible for nonce lifetime — pass the current counter in,
 * the function increments it by 1 and writes the new value back via the
 * in/out reference. This keeps the Python nonce counters (send_nonce /
 * recv_nonce) perfectly in sync after each C++ call.
 */

#include <cstdint>
#include <stdexcept>
#include <vector>

static constexpr size_t AEAD_NONCE_LEN = 12;
static constexpr size_t AEAD_TAG_LEN   = 16;
static constexpr size_t AEAD_KEY_LEN   = 32;

// The fixed AAD string used by FIshare ("FIshare", 7 bytes; matches Python).
static const uint8_t AEAD_AAD[]   = {'F', 'I', 's', 'h', 'a', 'r', 'e'};
static constexpr size_t AEAD_AAD_LEN = sizeof(AEAD_AAD);

/**
 * Write a uint64_t counter as a 12-byte big-endian nonce (matching Python's
 * n.to_bytes(12, "big")).  The counter occupies the last 8 bytes; the first
 * 4 bytes are zero, matching Python's AEADStream._n2b().
 */
void nonce_from_counter(uint64_t counter, uint8_t nonce[AEAD_NONCE_LEN]);

/**
 * Encrypt plaintext with ChaCha20-Poly1305.
 *
 * @param key          32-byte key
 * @param nonce_ctr    Current nonce counter (in/out, incremented on return)
 * @param plain        Plaintext buffer
 * @param plain_len    Plaintext length
 * @param out          Output buffer — must be at least (plain_len + AEAD_TAG_LEN) bytes
 *
 * Output layout: [ciphertext (plain_len bytes)][tag (16 bytes)]
 *
 * @throws std::runtime_error on OpenSSL failure
 */
void aead_encrypt(const uint8_t  key[AEAD_KEY_LEN],
                  uint64_t&      nonce_ctr,
                  const uint8_t* plain,
                  size_t         plain_len,
                  uint8_t*       out);

/**
 * Decrypt & verify a ChaCha20-Poly1305 ciphertext+tag.
 *
 * @param key            32-byte key
 * @param nonce_ctr      Current nonce counter (in/out, incremented on return)
 * @param cipher_and_tag Buffer containing ciphertext followed by 16-byte tag
 * @param ct_len         Total length (ciphertext + tag) — must be >= AEAD_TAG_LEN
 * @param out            Output buffer — must be at least (ct_len - AEAD_TAG_LEN) bytes
 *
 * @throws std::runtime_error on authentication failure or OpenSSL error
 */
void aead_decrypt(const uint8_t  key[AEAD_KEY_LEN],
                  uint64_t&      nonce_ctr,
                  const uint8_t* cipher_and_tag,
                  size_t         ct_len,
                  uint8_t*       out);
