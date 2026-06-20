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
    candidates = _candidate_grabbers(path, display, region)
    if not candidates:
        raise ScreenshotError(
            "no screenshot grabber available: install one of ffmpeg, "
            "imagemagick (import), scrot (X11), or grim / spectacle (Wayland)."
        )

    errors: list[str] = []
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
        return {
            "path": path, "width": width, "height": height,
            "region": list(region) if region is not None else None,
            "region_applied": bool(applied),
        }

    detail = "; ".join(errors) if errors else "no candidate tool was on PATH"
    raise ScreenshotError(f"all screenshot grabbers failed ({detail}).")


def _candidate_grabbers(
    path: str, display: str | None, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    """Build the ordered (tool, argv) candidate list for this display server."""
    if _is_wayland():
        return _wayland_grabbers(path, region)
    return _x11_grabbers(path, display, region)


def _is_wayland() -> bool:
    """True when the session is Wayland (so X11 grabbers won't work)."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _x11_grabbers(
    path: str, display: str | None, region: tuple[int, int, int, int] | None
) -> list[tuple[str, list[str]]]:
    """ffmpeg x11grab, ImageMagick import, then scrot — in preference order.

    With a ``region`` each grabber is given its native geometry flags so the
    captured image is genuinely cropped (not a full grab cropped afterward).
    """
    disp = display or os.environ.get("DISPLAY") or ":0"
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
