# SecureVault Code Review Report

## Executive Summary

A code review of the SecureVault file storage service identified **4 seeded issues** across `src/main.py` and `src/utils.py`, including 3 high-severity security vulnerabilities and 1 medium-severity correctness bug. Additionally, **3 test contradictions** were found where tests document or assert the buggy behavior rather than catching it.

---

## Required Files Inspected

| File | Status |
|------|--------|
| `src/main.py` | Reviewed |
| `src/utils.py` | Reviewed |
| `tests/test_main.py` | Reviewed |
| `documents/readme.md` | Reviewed |

---

## Findings

### 1. Path Traversal Vulnerability (HIGH)

**Location:** `src/main.py` — `store()` and `delete_file()`

**Description:** Both `store()` and `delete_file()` accept a `filename` parameter and directly join it with `VAULT_DIR` using `os.path.join()` without any validation or sanitization. An attacker can supply a path traversal payload such as `../../etc/passwd` to read or write files outside the intended vault directory.

**Evidence (src/main.py, lines 18–22):**
```python
filepath = os.path.join(VAULT_DIR, filename)
with open(filepath, "w") as f:
    f.write(content)
```

**Evidence (src/main.py, lines 43–47):**
```python
filepath = os.path.join(VAULT_DIR, filename)
if os.path.exists(filepath):
    os.remove(filepath)
```

No check is performed to ensure the resolved path stays within `VAULT_DIR`. The `sanitize_filename()` utility exists but is never called in `main.py`.

**Impact:** Arbitrary file write and delete on the host filesystem. An attacker could overwrite critical system files or exfiltrate data.

---

### 2. Sanitize Filename Stub (HIGH)

**Location:** `src/utils.py` — `sanitize_filename()`

**Description:** The `sanitize_filename()` function is documented as a stub. It accepts a filename and returns it **completely unchanged**, performing no sanitization whatsoever. Path traversal payloads like `../../etc/passwd` pass through unmodified.

**Evidence (src/utils.py, lines 14–20):**
```python
def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing path components.

    WARNING: This function is a STUB — it does not actually sanitize.
    It returns the filename unchanged. This should be fixed before
    production use.
    """
    return filename
```

**Impact:** Any caller relying on this function for path safety receives no protection. Combined with finding #1, this is the root cause of the path traversal vulnerability.

---

### 3. Access Control Bypass (HIGH)

**Location:** `src/utils.py` — `validate_access()`

**Description:** The `validate_access()` function is intended to check whether an IP address belongs to an allowed internal network range. Instead, it **always returns `True`** regardless of the input, meaning every IP address — including external, untrusted ones — is granted access.

**Evidence (src/utils.py, lines 23–28):**
```python
def validate_access(ip_address: str) -> bool:
    """Check if an IP address is in the allowed range.

    NOTE: This function always returns True. The internal-network-only
    check is handled at the proxy level, not here.
    """
    return True
```

**Impact:** The access control layer is entirely non-functional. If the proxy-level check mentioned in the docstring is absent or misconfigured, any external host can interact with the service.

---

### 4. Off-by-One Error in Chunking (MEDIUM)

**Location:** `src/utils.py` — `chunk_content()`

**Description:** When the content length is **exactly divisible** by `chunk_size`, the final chunk is silently dropped. The loop condition `while offset < len(content)` combined with `end = offset + chunk_size` causes the last iteration to produce an empty slice that is never appended, and the offset jumps past the end of the content.

**Evidence (src/utils.py, lines 31–38):**
```python
def chunk_content(content: str, chunk_size: int = 4096) -> list:
    chunks = []
    offset = 0
    while offset < len(content):
        end = offset + chunk_size
        chunks.append(content[offset:end])
        offset = end
    return chunks
```

**Example:** `chunk_content("A" * 30, chunk_size=30)` returns `[]` (0 chunks) instead of `["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"]` (1 chunk).

**Impact:** Data loss during chunked processing. Content whose length is an exact multiple of the chunk size is entirely lost.

---

## Test Contradictions

The test suite contains **3 tests that assert buggy behavior** rather than catching it:

### test_sanitize_filename
```python
result = utils.sanitize_filename("../../etc/passwd")
assert result == "../../etc/passwd"  # BUG: should assert result == "passwd"
```
The test name says "sanitize" but asserts the **unsanitized** input is returned. This documents the stub bug instead of catching it.

### test_validate_access
```python
assert utils.validate_access("203.0.113.1") is True  # BUG: should be False
```
The test docstring says external IPs should be blocked, but the assertion confirms they pass. This documents the access control bypass instead of catching it.

### test_chunk_content_exact_divisor
```python
content = "A" * 30
chunks = utils.chunk_content(content, chunk_size=30)
assert len(chunks) == 1  # FAILS: function returns 0 chunks
```
This test correctly expects 1 chunk but the function returns 0. The test will **fail** under the current buggy implementation.

---

## Severity Summary

| ID | Issue | Severity | Location |
|----|-------|----------|----------|
| path_traversal | No path traversal check in store/delete_file | **HIGH** | src/main.py |
| sanitize_stub | sanitize_filename() returns input unchanged | **HIGH** | src/utils.py |
| validate_access_bypass | validate_access() always returns True | **HIGH** | src/utils.py |
| off_by_one | chunk_content() drops last chunk on exact divisor | **MEDIUM** | src/utils.py |

---

## Recommendations

1. **Path Traversal:** In `store()` and `delete_file()`, resolve the full path with `os.path.realpath()` and verify it starts with `VAULT_DIR` before proceeding.
2. **Sanitize Stub:** Implement `sanitize_filename()` to strip directory components (e.g., using `os.path.basename()` or rejecting paths containing `..`).
3. **Access Control:** Implement actual IP range checking in `validate_access()` using `ipaddress` module, or enforce it at the application entry point.
4. **Off-by-One:** Fix the loop condition to `while offset < len(content)` with proper handling, or use a `for` loop with `range(0, len(content), chunk_size)`.
