"""Fetch the pinned Windows OpenSSL runtime used by the Python 3.10 build."""

import argparse
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tarfile
from urllib.request import urlopen
from zipfile import ZipFile

import zstandard


URL = ("https://conda.anaconda.org/conda-forge/win-64/"
       "openssl-1.1.1w-hcfcfb64_0.conda")
SHA256 = "6d46986fab161cb1b62f019c06dfc27f2e15caccd3943b3282d9e59872fa4ad2"
DLLS = ("libssl-1_1-x64.dll", "libcrypto-1_1-x64.dll")


def prepare(destination):
    with urlopen(URL, timeout=60) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != SHA256:
        raise ValueError("OpenSSL package SHA-256 mismatch")
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    found = set()
    with ZipFile(BytesIO(payload)) as archive:
        packages = [name for name in archive.namelist()
                    if name.startswith("pkg-") and name.endswith(".tar.zst")]
        if len(packages) != 1:
            raise ValueError("Unexpected conda package contents")
        with archive.open(packages[0]) as compressed:
            with zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
                with tarfile.open(fileobj=stream, mode="r|") as files:
                    for member in files:
                        if member.name not in {"Library/bin/" + name for name in DLLS}:
                            continue
                        if not member.isfile():
                            raise ValueError("OpenSSL DLL must be a regular file")
                        # Only copy the two expected files, never extract arbitrary paths.
                        with files.extractfile(member) as source:
                            (destination / Path(member.name).name).write_bytes(source.read())
                        found.add(Path(member.name).name)
    if found != set(DLLS):
        raise ValueError("OpenSSL DLLs missing from package")
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write(f"BLOOM_OPENSSL_DIR={destination}\n")
    print(f"Verified OpenSSL 1.1.1w: {destination}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/ci/openssl"))
    prepare(parser.parse_args().output)
