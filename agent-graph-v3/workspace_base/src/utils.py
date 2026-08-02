# utils.py
def hash_password(pw):
    import hashlib
    return hashlib.md5(pw.encode()).hexdigest()
