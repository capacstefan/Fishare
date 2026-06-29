# Documentație teste — Fishare

Acest document descrie toate testele automate din proiect: ce verifică, cum rulează și ce rezultat au avut la ultima rulare.

## Rulare

Din rădăcina proiectului:

```bash
python -m pytest tests/
```

Opțiuni utile:

```bash
python -m pytest tests/ -v          # listă detaliată per test
python -m pytest tests/ -m integration   # doar teste end-to-end cu socket/TLS
```

Configurația este în `pytest.ini`: testele sunt în `tests/`, markerul `integration` marchează testele cu socket real și TLS.

## Infrastructură comună

### `tests/conftest.py`

Înainte de importul pachetului:

- redirecționează `APPDATA`, `HOME` și `USERPROFILE` către un director temporar — testele **nu ating** datele reale ale utilizatorului (settings, history, certificate);
- face pachetul `fishare` importabil indiferent de layout;
- curăță automat fișierele de storage la începutul fiecărui test (`settings.json`, `history.json`, etc.).

**Fixtures:**

| Fixture                | Rol                                                            |
| ---------------------- | -------------------------------------------------------------- |
| `tmp_download_dir`     | director izolat pentru descărcări                              |
| `free_tcp_port`        | port TCP liber pe `127.0.0.1` pentru servere de test           |
| `_clean_storage_files` | (autouse) șterge fișierele persistente înainte de fiecare test |

### Rezultat ultimă rulare

| Metrică         | Valoare                               |
| --------------- | ------------------------------------- |
| **Data**        | 26 iunie 2026                         |
| **Platformă**   | Windows, Python 3.12.10, pytest 9.1.1 |
| **Total teste** | **93**                                |
| **Reușite**     | **93**                                |
| **Eșuate**      | **0**                                 |
| **Durată**      | ~1,9 s                                |

Toate testele au trecut (`93 passed`).

---

## `tests/test_crypto_utils.py` — Certificate TLS (4 teste)

Modul testat: `fishare.crypto_utils` — generarea certificatului autofirmat folosit la transferurile TLS.

| Test                                                            | Ce verifică                                                                        | Rezultat |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------- |
| `TestEnsureCert::test_creates_files_when_missing`               | Dacă lipsește certificatul, `ensure_cert()` creează `cert.pem` și `key.pem` nenule | ✅ PASS  |
| `TestEnsureCert::test_idempotent_when_already_present`          | A doua apelare nu regenerează fișierele (conținut identic)                         | ✅ PASS  |
| `TestEnsureCert::test_cert_is_valid_x509_with_expected_subject` | Certificatul este X.509 valid, cu numele aplicației în subject                     | ✅ PASS  |
| `TestEnsureCert::test_key_loads_as_rsa`                         | Cheia privată este RSA cu cel puțin 2048 biți                                      | ✅ PASS  |

---

## `tests/test_discovery.py` — Descoperire peeri (9 teste)

Modul testat: `fishare.discovery` — logică Peer / PeerRegistry (fără rețea mDNS reală).

| Test                                                               | Ce verifică                                                                      | Rezultat |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------- | -------- |
| `TestPeer::test_display_online`                                    | Peer online afișează numele și indicatorul 🟢                                    | ✅ PASS  |
| `TestPeer::test_display_offline_and_muted`                         | Peer offline + mut afișează 🔴 și 🔇                                             | ✅ PASS  |
| `TestPeerIdStability::test_peer_id_is_deterministic_for_same_cert` | `_peer_id()` returnează același ID hex (16 caractere) pentru același certificat  | ✅ PASS  |
| `TestPeerRegistryMute::test_default_state`                         | Registry nou: muted gol, online=True                                             | ✅ PASS  |
| `TestPeerRegistryMute::test_initial_muted_set`                     | Setul inițial de peeri mutați este respectat                                     | ✅ PASS  |
| `TestPeerRegistryMute::test_toggle_mute_returns_new_state`         | `toggle_mute` comută starea și returnează valoarea nouă                          | ✅ PASS  |
| `TestPeerRegistryMute::test_toggle_mute_updates_existing_peer`     | La mute, peer-ul existent primește `muted=True` și emite semnalul `peer_updated` | ✅ PASS  |
| `TestPeerRegistryMute::test_set_device_name_falls_back_when_empty` | Nume dispozitiv gol → se păstrează un nume implicit nenul                        | ✅ PASS  |
| `TestPeerRegistryMute::test_find_by_name`                          | Căutare peer după nume afișat; inexistent → `None`                               | ✅ PASS  |

---

## `tests/test_native.py` — SHA256 nativ (5 teste)

Modul testat: `fishare.native` — hash SHA256 în DLL nativ (`p2p_native.dll`).

**Notă:** Dacă DLL-ul nu poate fi încărcat, întreg modulul este sărit (`pytest.mark.skipif`).

| Test                                                    | Ce verifică                                     | Rezultat |
| ------------------------------------------------------- | ----------------------------------------------- | -------- |
| `TestNativeSha::test_empty`                             | Hash pe input gol = același ca `hashlib.sha256` | ✅ PASS  |
| `TestNativeSha::test_single_update`                     | Un singur `update()` produce digest corect      | ✅ PASS  |
| `TestNativeSha::test_multi_update_matches_concatenated` | Mai multe `update()` = hash pe concatenare      | ✅ PASS  |
| `TestNativeSha::test_large_input`                       | Input ~4 MB procesat corect                     | ✅ PASS  |
| `TestNativeSha::test_double_finalize_raises`            | Al doilea `hexdigest()` aruncă `RuntimeError`   | ✅ PASS  |

---

## `tests/test_network.py` — Transfer rețea TLS (19 teste)

Modul testat: `fishare.network` — server, task expeditor, coadă, limite dimensiune.

Folosește socket loopback real, TLS și certificatul generat. Testele `@pytest.mark.integration` pornesc un `TransferServer` pe un port liber.

### Clase de date și limite

| Test                                                   | Ce verifică                                                  | Rezultat |
| ------------------------------------------------------ | ------------------------------------------------------------ | -------- |
| `TestFileSpec::test_name_property_strips_dir`          | `FileSpec.name` returnează doar numele fișierului, fără cale | ✅ PASS  |
| `TestFileSizeLimit::test_single_file_within_limit`     | Un fișier sub `MAX_FILE_SIZE` → nu depășește limita          | ✅ PASS  |
| `TestFileSizeLimit::test_single_file_over_limit`       | Un fișier peste limită → `exceeds_file_size_limit` = True    | ✅ PASS  |
| `TestFileSizeLimit::test_batch_total_over_limit`       | Suma a două fișiere (60+60) peste limită 100 → respins       | ✅ PASS  |
| `TestFileSizeLimit::test_declared_total_over_limit`    | `declared_total` din ofertă peste limită → respins           | ✅ PASS  |
| `TestFileSizeLimit::test_empty_batch`                  | Listă goală → nu depășește limita                            | ✅ PASS  |
| `TestIncomingOffer::test_respond_unblocks_wait`        | `respond()` deblochează `wait()` cu accept + PIN             | ✅ PASS  |
| `TestIncomingOffer::test_wait_times_out_returns_false` | Timeout fără răspuns → `(False, …)`                          | ✅ PASS  |

### Anulare și task invalid

| Test                                                                     | Ce verifică                                        | Rezultat |
| ------------------------------------------------------------------------ | -------------------------------------------------- | -------- |
| `TestTransferTaskCancel::test_cancel_returns_true_first_time_then_false` | `cancel()` idempotent: True prima dată, apoi False | ✅ PASS  |
| `TestTransferTaskCancel::test_run_unknown_kind_emits_failed`             | Tip transfer necunoscut → `finished(False, …)`     | ✅ PASS  |
| `TestTransferTaskCancel::test_pre_cancel_short_circuits`                 | Anulare înainte de `run()` → motiv `"cancelled"`   | ✅ PASS  |

### Integrare end-to-end (`@integration`)

| Test                                                     | Ce verifică                                                                  | Rezultat |
| -------------------------------------------------------- | ---------------------------------------------------------------------------- | -------- |
| `TestEndToEndTransfer::test_text_transfer`               | Quick Text P2P: expeditor → server → semnal `text_received` cu textul corect | ✅ PASS  |
| `TestEndToEndTransfer::test_file_transfer_integrity`     | Transfer ~1 MB: bytes salvați identici cu sursa                              | ✅ PASS  |
| `TestEndToEndTransfer::test_offline_state_rejects_offer` | Destinatar offline respinge oferta cu `"offline"`                            | ✅ PASS  |
| `TestEndToEndTransfer::test_muted_sender_rejected`       | Expeditor în lista muted → respingere cu `"mute"`                            | ✅ PASS  |
| `TestEndToEndTransfer::test_recipient_rejection`         | Destinatar respinge manual → transfer eșuează                                | ✅ PASS  |
| `TestEndToEndTransfer::test_oversized_batch_rejected`    | Lot 60+60 bytes cu limită 100 → server respinge `"too large"`                | ✅ PASS  |

### Coadă transferuri

| Test                                                           | Ce verifică                                                | Rezultat |
| -------------------------------------------------------------- | ---------------------------------------------------------- | -------- |
| `TestTransferQueue::test_runs_submitted_task`                  | Task trimis la coadă este executat                         | ✅ PASS  |
| `TestTransferQueue::test_cancelled_before_submit_does_not_run` | Task deja anulat la submit → `"cancelled"`, fără conectare | ✅ PASS  |

---

## `tests/test_protocol.py` — Protocol wire (11 teste)

Modul testat: `fishare.protocol` — cadre length-prefixed JSON/date pe socket.

| Test                                                           | Ce verifică                                           | Rezultat |
| -------------------------------------------------------------- | ----------------------------------------------------- | -------- |
| `TestWireFraming::test_send_and_recv_json`                     | Trimite/primește obiect JSON                          | ✅ PASS  |
| `TestWireFraming::test_send_and_recv_data`                     | Trimite/primește payload binar (~15 KB)               | ✅ PASS  |
| `TestWireFraming::test_empty_data_frame`                       | Cadru date gol permis                                 | ✅ PASS  |
| `TestWireFraming::test_recv_json_wrong_type_raises`            | `recv_json()` pe cadru date → `WireError`             | ✅ PASS  |
| `TestWireFraming::test_send_data_too_large_raises`             | Payload > `MAX_FRAME` (4 MB) → `WireError`            | ✅ PASS  |
| `TestWireFraming::test_closed_socket_raises_connection_error`  | Socket închis → eroare la recv                        | ✅ PASS  |
| `TestWireFraming::test_interleaved_json_and_data`              | Secvență JSON + date + JSON păstrată                  | ✅ PASS  |
| `TestWireFraming::test_send_lock_serialises_concurrent_writes` | Două thread-uri trimit simultan — fără corupere cadre | ✅ PASS  |
| `TestTuneAndContexts::test_server_ctx_loads_cert`              | Context SSL server se încarcă                         | ✅ PASS  |
| `TestTuneAndContexts::test_client_ctx_skips_verification`      | Client TLS fără verificare hostname/cert              | ✅ PASS  |
| `TestTuneAndContexts::test_tune_does_not_raise_on_real_socket` | `tune(socket)` nu aruncă excepții                     | ✅ PASS  |

---

## `tests/test_storage.py` — Persistență JSON (14 teste)

Modul testat: `fishare.storage` — settings, history, quicktexts, muted.

| Test                                                     | Ce verifică                                                         | Rezultat |
| -------------------------------------------------------- | ------------------------------------------------------------------- | -------- |
| `TestSettings::test_defaults_when_missing`               | Fișier lipsă → valori implicite (device_name, online, download_dir) | ✅ PASS  |
| `TestSettings::test_save_and_reload_roundtrip`           | Salvare + reload păstrează toate câmpurile                          | ✅ PASS  |
| `TestSettings::test_defaults_merge_with_partial_file`    | Fișier parțial → restul câmpurilor din default                      | ✅ PASS  |
| `TestSettings::test_corrupt_file_falls_back_to_defaults` | JSON invalid → fallback la default                                  | ✅ PASS  |
| `TestHistory::test_empty_on_first_load`                  | History gol la prima încărcare                                      | ✅ PASS  |
| `TestHistory::test_append_and_read`                      | Append păstrează ordinea cronologică (cel mai vechi primul)         | ✅ PASS  |
| `TestHistory::test_clear`                                | `clear_history()` golește lista                                     | ✅ PASS  |
| `TestHistory::test_append_is_thread_safe`                | 4×10 append-uri concurente → 40 intrări, fără erori                 | ✅ PASS  |
| `TestQuickTexts::test_default_empty`                     | Quick texts gol implicit                                            | ✅ PASS  |
| `TestQuickTexts::test_save_roundtrip`                    | Salvare + reload quick texts                                        | ✅ PASS  |
| `TestMuted::test_default_empty_set`                      | Muted peers gol implicit                                            | ✅ PASS  |
| `TestMuted::test_save_and_reload`                        | Set muted persistat corect                                          | ✅ PASS  |
| `TestMuted::test_saved_format_is_sorted_list`            | Fișier JSON: listă sortată alfabetic                                | ✅ PASS  |
| `TestAtomicWrite::test_no_partial_file_left_on_success`  | După scriere reușită, fișierul `.tmp` nu rămâne                     | ✅ PASS  |

---

## `tests/test_sync.py` — Sincronizare folder (7 teste)

Modul testat: `fishare.sync` — SyncSender / SyncReceiver pe `socketpair` (fără TLS).

| Test                                                                  | Ce verifică                                                         | Rezultat |
| --------------------------------------------------------------------- | ------------------------------------------------------------------- | -------- |
| `TestSafePath::test_normal_path_resolves_inside_dest`                 | Cale relativă normală rămâne în folderul destinație                 | ✅ PASS  |
| `TestSafePath::test_parent_escape_returns_none`                       | `../evil.txt` → `_safe()` returnează `None` (path traversal blocat) | ✅ PASS  |
| `TestSafePath::test_absolute_paths_are_neutralised`                   | Căi absolute sunt normalizate relativ la destinație                 | ✅ PASS  |
| `TestSafePath::test_backslash_paths_normalised`                       | Separator `\` acceptat pe Windows                                   | ✅ PASS  |
| `TestPutAndDeleteRoundtrip::test_sender_initial_scan_transfers_files` | Scan inițial: fișiere + subfolder replicate cu conținut identic     | ✅ PASS  |
| `TestPutAndDeleteRoundtrip::test_delete_event_removes_file`           | Eveniment `delete` șterge fișierul din destinație                   | ✅ PASS  |
| `TestRelHelper::test_rel_uses_forward_slashes`                        | `_rel()` folosește `/` indiferent de OS                             | ✅ PASS  |

---

## `tests/test_util.py` — Funcții utilitare (14 teste)

Modul testat: `fishare.util` — formatare, căi unice, IP local.

| Test                                                | Ce verifică                                                | Rezultat |
| --------------------------------------------------- | ---------------------------------------------------------- | -------- |
| `TestFmtSize::test_bytes`                           | 0 B, 512 B                                                 | ✅ PASS  |
| `TestFmtSize::test_kilobytes`                       | 2048 → „2.0 KB”                                            | ✅ PASS  |
| `TestFmtSize::test_megabytes`                       | 5 MB formatat corect                                       | ✅ PASS  |
| `TestFmtSize::test_gigabytes`                       | 3 GB formatat corect                                       | ✅ PASS  |
| `TestFmtSize::test_terabytes`                       | 2 TB formatat corect                                       | ✅ PASS  |
| `TestFmtEta::test_zero_or_negative_bps`             | Viteză ≤ 0 → `"--"`                                        | ✅ PASS  |
| `TestFmtEta::test_seconds`                          | ETA în secunde                                             | ✅ PASS  |
| `TestFmtEta::test_minutes`                          | ETA minute + secunde (ex. 2m10s)                           | ✅ PASS  |
| `TestFmtEta::test_hours`                            | ETA ore + minute (ex. 1h01m)                               | ✅ PASS  |
| `TestUniquePath::test_returns_same_path_if_missing` | Fișier inexistent → calea originală                        | ✅ PASS  |
| `TestUniquePath::test_appends_counter_when_exists`  | Conflict → `file (1).txt`                                  | ✅ PASS  |
| `TestUniquePath::test_increments_until_free`        | `(1)`, `(2)` … până găsește nume liber                     | ✅ PASS  |
| `TestUniquePath::test_handles_no_extension`         | Fișiere fără extensie → `README (1)`                       | ✅ PASS  |
| `TestLocalIp::test_returns_ipv4_string`             | `local_ip()` returnează string IPv4 valid (4 octeți 0–255) | ✅ PASS  |

---

## `tests/test_web_server.py` — Server QR / upload web (10 teste)

Modul testat: `fishare.web_server` — Flask test client (fără bind socket real).

| Test                                                  | Ce verifică                                             | Rezultat |
| ----------------------------------------------------- | ------------------------------------------------------- | -------- |
| `TestRouting::test_index_requires_valid_token`        | Token greșit → HTTP 404                                 | ✅ PASS  |
| `TestRouting::test_index_with_valid_token_renders`    | Token valid → pagină 200 cu numele dispozitivului       | ✅ PASS  |
| `TestUpload::test_upload_saves_file_and_emits_signal` | Upload salvează fișierul și emite `file_received`       | ✅ PASS  |
| `TestUpload::test_upload_rejects_no_files`            | POST fără fișiere → mesaj „No files”                    | ✅ PASS  |
| `TestUpload::test_too_many_files`                     | Peste `WEB_MAX_FILES` (20) → mesaj „Too many”           | ✅ PASS  |
| `TestUpload::test_upload_unique_filenames`            | Conflict nume → suffix `(1)`                            | ✅ PASS  |
| `TestTextEndpoint::test_send_text_ok`                 | Text POST → semnal `text_received` cu „Phone” ca sender | ✅ PASS  |
| `TestTextEndpoint::test_send_text_empty_rejected`     | Text gol/whitespace → respins                           | ✅ PASS  |
| `TestTextEndpoint::test_text_truncated_to_max_chars`  | Text > 500 caractere tăiat la limită                    | ✅ PASS  |
| `TestTextEndpoint::test_text_requires_valid_token`    | Token invalid pe `/text` → 404                          | ✅ PASS  |

---

## Acoperire pe zone ale aplicației

| Zonă                       | Acoperit de teste | Neacoperit                                   |
| -------------------------- | ----------------- | -------------------------------------------- |
| Protocol wire + TLS        | ✅ Da             | Handshake E2E complet                        |
| Transfer fișier/text P2P   | ✅ Da             | Multi-fișier, PIN, anulare mid-flight        |
| Limite dimensiune (server) | ✅ Da             | Validare GUI expeditor (total 50 GB)         |
| Storage JSON               | ✅ Da             | Pinning peer (`check_and_pin`)               |
| Sync folder                | ✅ Parțial        | Fișiere prea mari, oprire sync, hash invalid |
| QR web server              | ✅ Da             | Upload total > 4 GB (HTTP 413)               |
| Discovery mDNS             | ✅ Logică only    | Anunț real pe LAN                            |
| GUI (PyQt6)                | ❌ Nu             | Tab-uri, dialoguri, main window              |
| Native SHA256              | ✅ Da\*           | \*Doar dacă DLL disponibil                   |

---

## Rezumat pe fișiere

| Fișier                 | Teste  | Rezultat     |
| ---------------------- | ------ | ------------ |
| `test_crypto_utils.py` | 4      | 4/4 ✅       |
| `test_discovery.py`    | 9      | 9/9 ✅       |
| `test_native.py`       | 5      | 5/5 ✅       |
| `test_network.py`      | 19     | 19/19 ✅     |
| `test_protocol.py`     | 11     | 11/11 ✅     |
| `test_storage.py`      | 14     | 14/14 ✅     |
| `test_sync.py`         | 7      | 7/7 ✅       |
| `test_util.py`         | 14     | 14/14 ✅     |
| `test_web_server.py`   | 10     | 10/10 ✅     |
| **Total**              | **93** | **93/93 ✅** |
