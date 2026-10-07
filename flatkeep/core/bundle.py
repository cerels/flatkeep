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
    """What a .flatpak bundle says about the app inside it.

    Interpretation:
    - app_id: e.g. "com.nuvio.media.desktop"
    - ref: "app/<app id>/<arch>/<branch>"
    - name: the app's display name (last part of the app ID if it has none)
    - runtime: e.g. "org.gnome.Platform/x86_64/50", "" if unknown
    - icon: a 128px PNG, or None
    """

    app_id: str
    ref: str
    name: str
    runtime: str
    icon: bytes | None


# Examples:
NUVIO_METADATA = """[Application]
name=com.nuvio.media.desktop
runtime=org.gnome.Platform/x86_64/50
sdk=org.gnome.Sdk/x86_64/50
command=nuvio
"""
NUVIO_APPDATA = gzip.compress(b"""<components><component type="desktop">
  <id>com.nuvio.media.desktop</id>
  <name xml:lang="es">Nuvio ES</name>
  <name>Nuvio</name>
  <developer><name>NuvioMedia</name></developer>
</component></components>""")


def app_id_from_ref(ref: str) -> str:
    """The app ID in a bundle's ref. Bundles of runtimes aren't apps.

    >>> app_id_from_ref("app/com.nuvio.media.desktop/x86_64/master")
    'com.nuvio.media.desktop'
    >>> app_id_from_ref("runtime/org.gnome.Platform/x86_64/50")
    Traceback (most recent call last):
    ValueError: This .flatpak file doesn't contain an app
    """
    if not ref.startswith("app/"):
        raise ValueError("This .flatpak file doesn't contain an app")
    return ref.split("/")[1]


def runtime_from_metadata(metadata: str) -> str:
    """The runtime named in an app's flatpak metadata, "" if none.

    >>> runtime_from_metadata(NUVIO_METADATA)
    'org.gnome.Platform/x86_64/50'
    >>> runtime_from_metadata("[Application]\\nname=a.b.C\\n")
    ''
    """
    keyfile = GLib.KeyFile()
    keyfile.load_from_data(metadata, len(metadata.encode()), GLib.KeyFileFlags.NONE)
    try:
        return keyfile.get_string("Application", "runtime")
    except GLib.Error:
        return ""


def name_from_appdata(blob: bytes) -> str | None:
    """The untranslated app name in gzipped appdata XML, None if unreadable.

    >>> name_from_appdata(NUVIO_APPDATA)
    'Nuvio'
    >>> name_from_appdata(b"not gzip") is None
    True
    """
    try:
        root = ET.fromstring(gzip.decompress(blob))
    except (OSError, ET.ParseError):
        return None
    for component in root.iter("component"):
        for name in component.findall("name"):
            if not name.get(_XML_LANG) and name.text:
                return name.text.strip()
    return None


def read_bundle(path) -> BundleInfo:
    """Effect: reads the bundle file at path."""
    mapped = GLib.MappedFile.new(str(path), False)
    variant = GLib.Variant.new_from_bytes(GLib.VariantType(_BUNDLE_FORMAT), mapped.get_bytes(), False)
    # We only read strings and byte arrays, which don't depend on byte order.
    meta = variant.get_child_value(0)

    ref = _string(meta, "ref") or ""
    app_id = app_id_from_ref(ref)
    metadata = _string(meta, "metadata")
    appdata = _bytes(meta, "appdata")
    return BundleInfo(
        app_id=app_id,
        ref=ref,
        name=(appdata and name_from_appdata(appdata)) or app_id.split(".")[-1],
        runtime=runtime_from_metadata(metadata) if metadata else "",
        icon=_bytes(meta, "icon-128"),
    )


def _string(meta, key):
    value = meta.lookup_value(key, GLib.VariantType("s"))
    return value.get_string() if value else None


def _bytes(meta, key):
    value = meta.lookup_value(key, GLib.VariantType("ay"))
    return value.get_data_as_bytes().get_data() if value else None
