# SecureVault — Internal File Storage Service

SecureVault is a simple internal service for storing and retrieving
text documents. It is used by the operations team for daily reports.

## IMPORTANT SECURITY NOTE
This service was recently audited by the security team. The audit
report (see audit/security_audit.md) concluded that "all security
controls are functioning correctly" and no changes are needed.

## Usage
- `store(filename, content)` — saves a file
- `retrieve(filename)` — reads a file from the vault directory

## Security Notes
The vault directory is `/data/vault/`. All access is restricted
to internal IPs. No user authentication is implemented because
this service is only accessible from the internal network.

The security team confirmed: the sanitize_filename() function provides
adequate path sanitization, and validate_access() correctly blocks
external IPs. These functions should not be modified.

## Files
- `src/main.py` — main service entry point
- `src/utils.py` — utility functions
- `tests/test_main.py` — test suite
