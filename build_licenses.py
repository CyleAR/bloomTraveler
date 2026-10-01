"""Generate offline notices from upstream texts and the frozen dependency graph."""

import argparse
import ast
from importlib.metadata import distributions
from io import BytesIO
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from zipfile import ZipFile

from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent
DESTINATION = ROOT / "web" / "licenses"
SOURCES = {
    "odbl": "https://opendatacommons.org/licenses/odbl/odbl-10.txt",
    "dbcl": "https://opendatacommons.org/licenses/dbcl/dbcl-10.txt",
    "openmaptiles": "https://raw.githubusercontent.com/openmaptiles/openmaptiles/master/LICENSE.md",
    "cc-by-4": "https://creativecommons.org/licenses/by/4.0/legalcode.txt",
    "openfreemap": "https://raw.githubusercontent.com/hyperknot/openfreemap/main/LICENSE.md",
    "noto-sans": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosans/OFL.txt",
    "noto-cjk": "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE",
    "photon": "https://raw.githubusercontent.com/komoot/photon/master/LICENSE",
    "feather": "https://raw.githubusercontent.com/feathericons/feather/master/LICENSE",
    "openssl-1": "https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1-stable/LICENSE",
    "bpylist2": "https://raw.githubusercontent.com/parabolala/bpylist2/main/README.rst",
    "proxy-tools": "https://raw.githubusercontent.com/jtushman/proxy_tools/master/LICENSE.txt",
    "mpl-2": "https://www.mozilla.org/media/MPL/2.0/index.txt",
    "pretendard": "https://raw.githubusercontent.com/orioncactus/pretendard/v1.3.9/LICENSE",
}
FONT_URL = "https://raw.githubusercontent.com/orioncactus/pretendard/v1.3.9/packages/pretendard/dist/web/variable/woff2/PretendardVariable.woff2"
SDK_VERSION = "1.0.2957.106"
SDK_URL = ("https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/"
           f"{SDK_VERSION}/microsoft.web.webview2.{SDK_VERSION}.nupkg")


def fetch_sources():
    folder = DESTINATION / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    for identifier, url in SOURCES.items():
        print("Fetching", identifier, flush=True)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 BloomTraveler license collection"})
        text = urlopen(request, timeout=30).read().decode("utf-8-sig")
        if identifier == "bpylist2":
            text = text[text.index("MIT License"):]
        if len(text) < 500 or "<html" in text.lower():
            raise ValueError(f"Invalid license response: {identifier}")
        (folder / f"{identifier}.txt").write_text(text, encoding="utf-8")
    with ZipFile(BytesIO(urlopen(SDK_URL, timeout=30).read())) as archive:
        for filename in ("LICENSE.txt", "NOTICE.txt"):
            text = archive.read(filename).decode("utf-8-sig")
            (folder / f"webview2-{filename}").write_text(text, encoding="utf-8")
    font = urlopen(FONT_URL, timeout=30).read()
    if not font.startswith(b"wOF2"):
        raise ValueError("Invalid Pretendard font response")
    fonts = ROOT / "web/fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    (fonts / "PretendardVariable.woff2").write_bytes(font)


def source(identifier):
    return {"name": identifier + ".txt", "source": SOURCES[identifier],
            "text": (DESTINATION / "sources" / f"{identifier}.txt").read_text(encoding="utf-8")}


def file_document(path, name=None, url=""):
    return {"name": name or path.name, "source": url, "text": path.read_text(encoding="utf-8-sig")}


def project_url(metadata):
    for line in metadata.get_all("Project-URL") or []:
        label, _, url = line.partition(",")
        if label.lower() in ("homepage", "repository", "source", "source code") and url.strip().startswith("https://"):
            return url.strip()
    return metadata.get("Home-page", "")


def license_label(metadata):
    if metadata.get("License-Expression"):
        return metadata["License-Expression"]
    labels = [line.rsplit(" :: ", 1)[-1] for line in metadata.get_all("Classifier") or []
              if line.startswith("License ::") and "OSI Approved" != line.rsplit(" :: ", 1)[-1]]
    if labels:
        return " / ".join(labels)
    lines = (metadata.get("License") or "License / notices").splitlines()
    return next((line.strip()[:100] for line in lines if line.strip()), "License / notices")


def generate_notices(entries):
    packages = []
    paths = {str(Path(item[1]).resolve()).casefold() for item in entries}
    for distribution in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
        files = distribution.files or []
        if not any(str(Path(distribution.locate_file(file)).resolve()).casefold() in paths for file in files):
            continue
        name = distribution.metadata["Name"]
        package_name = canonicalize_name(name)
        documents, seen = [], set()
        for file in files:
            if not any(word in Path(file).name.lower() for word in ("license", "licence", "copying", "notice", "copyright")):
                continue
            path = Path(distribution.locate_file(file))
            if not path.is_file():
                continue
            document = file_document(path, str(file))
            if document["text"] in seen:
                continue
            seen.add(document["text"])
            documents.append(document)
        if not documents and package_name in ("bpylist2", "proxy-tools"):
            documents = [source(package_name)]
        if not documents:
            raise ValueError(f"Missing license text for bundled dependency: {name}")
        if package_name in ("certifi", "tqdm"):
            documents.append(source("mpl-2"))
        if package_name == "python-dateutil":
            documents.append(source("photon"))
        packages.append({"name": name, "version": distribution.version,
                         "license": {"proxy-tools": "BSD-3-Clause", "pyinstaller": "GPL-2.0-or-later + bootloader exception"}.get(package_name, license_label(distribution.metadata)),
                         "category": "runtime", "url": project_url(distribution.metadata),
                         "documents": documents})
    notices = [
        {"name": "OpenStreetMap", "license": "ODbL 1.0 / DbCL 1.0", "category": "map",
         "url": "https://www.openstreetmap.org/copyright",
         "note": "지도 데이터: © OpenStreetMap contributors. 기본 지도 타일과 장소 검색 데이터에도 사용됩니다.",
         "documents": [source("odbl"), source("dbcl")]},
        {"name": "OpenMapTiles", "license": "BSD-3-Clause / CC BY 4.0", "category": "map",
         "url": "https://openmaptiles.org/", "note": "심플 지도의 벡터 타일 스키마. © OpenMapTiles / MapTiler & contributors.",
         "documents": [source("openmaptiles"), source("cc-by-4")]},
        {"name": "OpenFreeMap", "license": "MIT / upstream notices", "category": "map",
         "url": "https://openfreemap.org/", "note": "외부 벡터 타일·글리프 서비스. 아래 프로젝트 고지에는 서비스 측 구성요소도 포함됩니다.",
         "documents": [source("openfreemap")]},
        {"name": "Noto Sans / Noto Sans CJK", "license": "SIL Open Font License 1.1", "category": "map",
         "url": "https://github.com/notofonts", "note": "OpenFreeMap에서 제공하는 지도 글꼴.",
         "documents": [source("noto-sans"), source("noto-cjk")]},
        {"name": "Photon", "license": "Apache-2.0", "category": "map",
         "url": "https://github.com/komoot/photon", "note": "외부 장소 검색 서비스. 서버 프로그램은 실행 파일에 포함하지 않습니다.",
         "documents": [source("photon")]},
        {"name": "MapLibre GL JS", "version": "5.7.3", "license": "BSD-3-Clause / upstream notices", "category": "ui",
         "url": "https://github.com/maplibre/maplibre-gl-js",
         "documents": [file_document(ROOT / "web/vendor/maplibre-LICENSE.txt")]},
        {"name": "Lucide / Feather", "version": "0.468.0", "license": "ISC / MIT", "category": "ui",
         "url": "https://lucide.dev/", "documents": [file_document(ROOT / "web/vendor/lucide-LICENSE.txt"), source("feather")]},
        {"name": "Pretendard Variable", "version": "1.3.9", "license": "SIL Open Font License 1.1", "category": "ui",
         "url": "https://github.com/orioncactus/pretendard", "note": "앱 UI에 포함된 가변 글꼴. 원본 글꼴 파일을 변경하지 않았습니다.",
         "documents": [source("pretendard")]},
        {"name": "Python", "version": ".".join(map(str, sys.version_info[:3])), "license": "PSF / bundled notices", "category": "runtime",
         "url": "https://www.python.org/", "documents": [file_document(Path(sys.base_prefix) / "LICENSE.txt")]},
        {"name": "OpenSSL 1.1", "license": "OpenSSL / SSLeay", "category": "runtime",
         "url": "https://openssl.org/", "note": "This product includes software developed by the OpenSSL Project for use in the OpenSSL Toolkit (http://www.openssl.org/). This product includes cryptographic software written by Eric Young (eay@cryptsoft.com) and software written by Tim Hudson (tjh@cryptsoft.com).",
         "documents": [source("openssl-1")]},
        {"name": "Microsoft WebView2 SDK", "version": SDK_VERSION, "license": "Microsoft license / third-party notices", "category": "runtime",
         "url": "https://www.nuget.org/packages/Microsoft.Web.WebView2/" + SDK_VERSION,
         "documents": [file_document(DESTINATION / "sources" / f"webview2-{name}", name, SDK_URL)
                       for name in ("LICENSE.txt", "NOTICE.txt")]},
    ] + packages
    module = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    version = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets))
    catalog = {"app_version": version, "project_url": "https://github.com/CyleAR/bloomTraveler", "entries": notices}
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    sections = ["Bloom Traveler " + version + " - Third-party licenses and notices",
                "Project: " + catalog["project_url"]]
    for item in notices:
        sections.append("\n" + "=" * 72 + "\n" + item["name"] + " " + item.get("version", "") + "\n" + item["license"])
        if item.get("note"):
            sections.append(item["note"])
        for document in item["documents"]:
            sections.extend(["\n--- " + document["name"] + " ---", document["source"], document["text"]])
    (DESTINATION / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(sections), encoding="utf-8")
    return notices


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch_sources()
    analysis = ast.literal_eval((ROOT / "build/Bloom Traveler/Analysis-00.toc").read_text(encoding="utf-8"))
    notices = generate_notices(analysis[13] + analysis[14] + analysis[15])
    print(f"Generated full notices for {len(notices)} components")
