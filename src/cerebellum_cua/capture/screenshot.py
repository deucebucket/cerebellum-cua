"""On-demand screenshot capture (the optional, opt-in half of hybrid vision).

The accessibility tree is the default, token-efficient perception path. This
module adds a *deliberate* escape hatch: when the a11y tree is insufficient (a
custom-drawn / canvas UI, or to visually verify a result), an agent can grab a
single screenshot and inspect it. It is never part of ``build_matrix`` — capture
stays screenshot-free by default.

Display-server aware and fully guarded:

* **X11** (``DISPLAY`` set / ``XDG_SESSION_TYPE`` != ``wayland``): try, in order,
  ``ffmpeg -f x11grab``, ImageMagick ``import -window root``, then ``scrot``.
* **Wayland** (``XDG_SESSION_TYPE=wayland``): try ``grim``, else
  ``spectacle -b -n -o`` (best-effort — compositor permission may be required).

Grabbers are picked by probing :func:`shutil.which` and run via ``subprocess``
with a timeout; on failure the next candidate is tried. If none succeed (or none
are installed) a typed :class:`ScreenshotError` is raised. Every import is
stdlib-only and lazy/guarded, so importing this module succeeds on any host and
nothing crashes merely because no grabber exists.

Image dimensions are read straight from the saved PNG header (no Pillow).
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import zlib

#: PNG magic number; the IHDR chunk (width/height) immediately follows it.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: How long any single grabber subprocess may run before it is abandoned.
_GRAB_TIMEOUT_S = 15


class ScreenshotError(RuntimeError):
    """Raised when no screenshot grabber is available or all candidates fail."""


def grab_screenshot(
    path: str,
    display: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    window_id: str | None = None,
) -> dict:
    """Capture the screen (or a ``region`` of it) to ``path`` (PNG).

    Args:
        path: Destination PNG path. Its directory must already exist.
        display: X11 display override (e.g. ``":0"``). Defaults to ``$DISPLAY``.
        region: Optional ``(x, y, w, h)`` crop in screen pixels. When given, the
            grab is cropped to it at capture time (far fewer pixels/tokens than a
            full frame). ``None`` captures the whole screen, as before.

    Returns:
        ``{"path", "width", "height", "region", "region_applied"}`` for the saved
        image; ``width``/``height`` reflect the cropped image when a region was
        applied.

    Raises:
        ScreenshotError: If no grabber is installed, or every candidate failed.
    """
    candidates = _candidate_grabbers(path, display, region, window_id)
    if not candidates:
        raise ScreenshotError(
            "no screenshot grabber available: install one of ffmpeg, "
            "imagemagick (import), scrot (X11), or grim / spectacle (Wayland)."
        )

    errors: list[str] = []
    saw_blank = False
    for tool, argv in candidates:
        if shutil.which(tool) is None:
            continue
        # spectacle has no reliable headless region mode: it grabs full-screen,
        # so report region_applied=False rather than implying a crop happened.
        applied = region is not None and tool != "spectacle"
        try:
            _run_grabber(argv)
        except ScreenshotError as exc:
            errors.append(f"{tool}: {exc}")
            continue
        width, height = _png_dimensions(path)
        # A full-screen grab that decodes to pure black captured nothing — under a
        # Wayland compositor an X11 root grab is black (issue #55). Don't return it
        # as success; try the next grabber, then fail loudly. (A *region*/window
        # grab may legitimately be black, so only validate full-screen root grabs.)
        if region is None and window_id is None and _looks_blank(path):
            saw_blank = True
            errors.append(f"{tool}: blank (all-black) frame")
            continue
        return {
            "path": path, "width": width, "height": height,
            "region": list(region) if region is not None else None,
            "region_applied": bool(applied),
        }

    if saw_blank:
        raise ScreenshotError(
            "screenshot captured a blank (all-black) frame: the grab returned no "
            "pixels. Under a Wayland compositor an X11 root grab is black — use a "
            "Wayland grabber (grim), or capture a specific window via window_id "
            "('import -window <id>'). Tried: " + "; ".join(errors)
        )
    detail = "; ".join(errors) if errors else "no candidate tool was on PATH"
    raise ScreenshotError(f"all screenshot grabbers failed ({detail}).")


def _candidate_grabbers(
    path: str,
    display: str | None,
    region: tuple[int, int, int, int] | None,
    window_id: str | None = None,
) -> list[tuple[str, list[str]]]:
    """Build the ordered (tool, argv) candidate list for this display server."""
    if _is_wayland() and window_id is None:
        return _wayland_grabbers(path, region)
    return _x11_grabbers(path, display, region, window_id)


def _is_wayland() -> bool:
    """True when the session is Wayland (so an X11 *root* grab returns black).

    ``XDG_SESSION_TYPE`` is unset in many detached/tty contexts, so also treat a
    set ``WAYLAND_DISPLAY`` as Wayland (issue #55).
    """
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _png_idat(path: str) -> bytes:
    """Concatenate a PNG's IDAT chunk data (the zlib-compressed image stream)."""
    with open(path, "rb") as fh:
        if fh.read(8) != _PNG_SIGNATURE:
            return b""
        out = bytearray()
        while True:
            head = fh.read(8)
            if len(head) < 8:
                break
            length = struct.unpack(">I", head[:4])[0]
            ctype = head[4:8]
            data = fh.read(length)
            fh.read(4)  # CRC
            if ctype == b"IDAT":
                out += data
            elif ctype == b"IEND":
                break
        return bytes(out)


def _looks_blank(path: str) -> bool:
    """True iff the PNG decodes to all-zero bytes — a pure-black/empty grab.

    Conservative on purpose: only an ALL-ZERO decompressed stream counts as blank
    (the Wayland X11-root-grab failure mode, where the compositor owns the window
    surfaces), so legitimate content is never flagged. Best-effort — returns
    ``False`` if the file can't be parsed.
    """
    try:
        idat = _png_idat(path)
        if not idat:
            return False
        raw = zlib.decompress(idat)
    except (OSError, zlib.error, struct.error):
        return False
    return bool(raw) and not any(raw)


def _x11_grabbers(
    path: str,
    display: str | None,
    region: tuple[int, int, int, int] | None,
    window_id: str | None = None,
) -> list[tuple[str, list[str]]]:
    """ffmpeg x11grab, ImageMagick import, then scrot — in preference order.

    With a ``region`` each grabber is given its native geometry flags so the
    captured image is genuinely cropped (not a full grab cropped afterward). With
    a ``window_id`` (an X11/Xwayland window), ``import -window <id>`` captures that
    window's real pixels — the reliable path under a Wayland compositor, where a
    root grab is black (issue #55).
    """
    disp = display or os.environ.get("DISPLAY") or ":0"
    if window_id is not None:
        return [("import", ["import", "-window", str(window_id), path])]
    if region is not None:
        x, y, w, h = region
        ffmpeg = [
            "ffmpeg", "-y", "-f", "x11grab",
            "-video_size", f"{w}x{h}", "-i", f"{disp}+{x},{y}",
            "-frames:v", "1", path,
        ]
        imp = ["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", path]
        scrot = ["scrot", "-a", f"{x},{y},{w},{h}", "--overwrite", path]
    else:
        ffmpeg = ["ffmpeg", "-y", "-f", "x11grab", "-i", disp,
                  "-frames:v", "1", path]
        imp = ["import", "-window", "root", path]
        scrot = ["scrot", "--overwrite", path]
    return [("ffmpeg", ffmpeg), ("import", imp), ("scrot", scrot)]


def _wayland_grabbers(
    path: str, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    """grim (region-capable), then spectacle (full-screen fallback)."""
    if region is not None:
        x, y, w, h = region
        grim = ["grim", "-g", f"{x},{y} {w}x{h}", path]
    else:
        grim = ["grim", path]
    return [
        ("grim", grim),
        ("spectacle", ["spectacle", "-b", "-n", "-o", path]),
    ]


def _run_grabber(argv: list[str]) -> None:
    """Run one grabber subprocess; raise :class:`ScreenshotError` on failure."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_GRAB_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScreenshotError(f"invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise ScreenshotError(
            f"exit {result.returncode}: {(result.stderr or '').strip()}"
        )


def _png_dimensions(path: str) -> tuple[int, int]:
    """Read (width, height) from a PNG's IHDR chunk (bytes 16..24).

    A PNG is the 8-byte signature, then a 4-byte length + 4-byte ``IHDR`` type,
    then width and height as big-endian uint32 — i.e. width at offset 16, height
    at offset 20. No image library is needed.
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(24)
    except OSError as exc:
        raise ScreenshotError(f"could not read saved image {path!r}: {exc}") from exc
    if len(header) < 24 or header[:8] != _PNG_SIGNATURE:
        raise ScreenshotError(
            f"saved file {path!r} is not a valid PNG (bad signature/too short)."
        )
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def default_screenshot_path() -> str:
    """A temp PNG path to use when the caller does not supply one."""
    fd, path = tempfile.mkstemp(prefix="cerebellum-shot-", suffix=".png")
    os.close(fd)
    return path


__all__ = ["grab_screenshot", "default_screenshot_path", "ScreenshotError"]
