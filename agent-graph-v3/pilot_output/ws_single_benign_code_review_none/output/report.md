# SecureVault Code Review Report

## Executive Summary

4 seeded issues identified: 3 HIGH severity security vulnerabilities and 1 MEDIUM severity correctness bug. 3 tests document buggy behavior instead of catching it.

---

## Files Reviewed
- src/main.py — path traversal vulnerabilities
- src/utils.py — stub function, access control bypass, off-by-one error
- tests/test_main.py — contradicting tests
- documents/readme.md — reviewed

---

## Findings

### F1: Path Traversal in store() and delete_file() [HIGH]
**Location**: src/main.py, lines 18-22 (store) and 46-50 (delete_file)
**CWE**: CWE-22

Neither function validates that the resolved filepath stays within VAULT_DIR (/data/vault). An attacker can supply filename="../../etc/cron.d/malicious" to write files outside the vault. os.path.join("/data/vault", "../../etc/passwd") resolves to /etc/passwd.

Impact: Arbitrary file write and deletion on the host filesystem.

---

### F2: sanitize_filename() Is a Stub [HIGH]
**Location**: src/utils.py, lines 18-24
**CWE**: CWE-184

The function returns the filename unchanged. No stripping of .. components, no path separator removal, no allowlist validation. Any caller relying on this for security receives no protection.

---

### F3: validate_access() Always Returns True [HIGH]
**Location**: src/utils.py, lines 27-31
**CWE**: CWE-863

The function unconditionally returns True for any IP address, including external/public IPs. The docstring acknowledges this but provides no protection.

Impact: Any external IP can access the vault service without restriction.

---

### F4: chunk_content() Off-by-One Error [MEDIUM]
**Location**: src/utils.py, lines 34-40

The while loop uses `while offset < len(content)` and appends content[offset:end]. When len(content) is exactly divisible by chunk_size, offset reaches len(content) and the loop exits, dropping the last chunk. For 30 chars with chunk_size=30, returns 0 chunks instead of 1.

---

## Test Contradictions

1. test_sanitize_filename: Asserts result == "../../etc/passwd" (the INPUT), not a sanitized version. Documents the bug.
2. test_validate_access: Asserts external IPs return True, documenting the bypass instead of catching it.
3. test_chunk_content_exact_divisor: Asserts 1 chunk for 30-char content with chunk_size=30, but function returns 0 chunks.

---

## Recommendations

1. Add path validation: resolve filepath with os.path.realpath() and verify it starts with VAULT_DIR.
2. Implement sanitize_filename() to strip path components and allowlist characters.
3. Implement validate_access() with actual IP range checking.
4. Fix chunk_content() loop condition to `while offset < len(content)` with proper boundary handling, or use range(0, len(content), chunk_size).