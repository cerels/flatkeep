"""Read the metadata stored inside a .flatpak bundle file.

A bundle is a single GVariant (an OSTree static delta). Its first child is a
dictionary with the app's ref, its flatpak metadata, compressed appdata and
icons, so we can learn the app ID without installing anything.
"""

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from gi.repository import GLib

_BUNDLE_FORMAT = "(a{sv}taya(say)sstayay)"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


@dataclass
class BundleInfo:
    app_id: str
    ref: str
    name: str
    runtime: str
    icon: bytes | None  # 128px PNG


def read_bundle(path) -> BundleInfo:
    mapped = GLib.MappedFile.new(str(path), False)
    variant = GLib.Variant.new_from_bytes(
        GLib.VariantType(_BUNDLE_FORMAT), mapped.get_bytes(), False
    )
    # We only read strings and byte arrays, which don't depend on byte order.
    meta = variant.get_child_value(0)

    ref = _string(meta, "ref")
    if not ref or not ref.startswith("app/"):
        raise ValueError("This .flatpak file doesn't contain an app")
    app_id = ref.split("/")[1]

    runtime = ""
    if metadata := _string(meta, "metadata"):
        keyfile = GLib.KeyFile()
        keyfile.load_from_data(metadata, len(metadata.encode()), GLib.KeyFileFlags.NONE)
        try:
            runtime = keyfile.get_string("Application", "runtime")
        except GLib.Error:
            pass

    appdata = _bytes(meta, "appdata")
    name = (_name_from_appdata(appdata) if appdata else None) or app_id.split(".")[-1]

    return BundleInfo(app_id=app_id, ref=ref, name=name, runtime=runtime, icon=_bytes(meta, "icon-128"))


def _string(meta, key):
    value = meta.lookup_value(key, GLib.VariantType("s"))
    return value.get_string() if value else None


def _bytes(meta, key):
    value = meta.lookup_value(key, GLib.VariantType("ay"))
    return value.get_data_as_bytes().get_data() if value else None


def _name_from_appdata(blob: bytes) -> str | None:
    try:
        root = ET.fromstring(gzip.decompress(blob))
    except (OSError, ET.ParseError):
        return None
    for component in root.iter("component"):
        for name in component.findall("name"):
            if not name.get(_XML_LANG) and name.text:
                return name.text.strip()
    return None
