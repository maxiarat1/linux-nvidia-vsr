"""Chromium needs a new extension version when its worker script URL changes."""


def local_version(release, digest):
    major, minor, *_ = release.split(".")
    return f"{int(major)}.{int(minor)}.{int(digest[:4], 16)}.{int(digest[4:8], 16)}"
