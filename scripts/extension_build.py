"""Chromium needs a new extension version when its worker script URL changes."""


def local_version(release, digest, previous=None):
    base = tuple(int(part) for part in release.split("."))
    if len(base) != 3 or any(not 0 <= part <= 65535 for part in base):
        raise ValueError("Release must have three Chromium-compatible version parts")
    old = (
        tuple(int(part) for part in previous["version"].split("."))
        if previous
        else None
    )
    if previous and previous.get("digest") == digest:
        return previous["version"]
    value = list(max(base + (0,), old or (0, 0, 0, 0)))
    for index in range(3, -1, -1):
        if value[index] < 65535:
            value[index] += 1
            break
        value[index] = 0
    else:
        raise ValueError("Extension build version space exhausted")
    return ".".join(map(str, value))
