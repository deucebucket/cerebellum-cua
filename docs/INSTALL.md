# Installation

`cerebellum-cua` requires Python 3.10 or newer. The package import name is
`cerebellum_cua`; the distribution name is `cerebellum-cua`.

## Install from source

The package is **not yet published to PyPI**, so `pip install cerebellum-cua`
does not work today. Install from a checkout of the repository instead:

```bash
git clone <repo-url>
cd cerebellum-cua
pip install -e .
```

This installs the core package and its only required runtime dependency, PyJWT.
Everything except live capture (the matrix logic, storage, gateway, protocol,
semantics) works after this step on any OS.

## Optional extras

| Extra        | Install                          | What it adds                                  | Platform        |
|--------------|----------------------------------|-----------------------------------------------|-----------------|
| `postgres`   | `pip install -e '.[postgres]'`   | `psycopg2-binary` for the PostgreSQL backend  | any             |
| `uia`        | `pip install -e '.[uia]'`        | `uiautomation`, `comtypes` for live UIA capture | Windows only  |
| `dev`        | `pip install -e '.[dev]'`        | `pytest`, `pytest-cov`, `ruff`, `mypy`        | any             |

Extras combine, e.g. `pip install -e '.[dev,postgres]'`.

The `uia` extra is Windows-only. Its dependencies are not installable on Linux or
macOS; do not add it to a non-Windows environment.

## Enabling live capture

Installing the package does not, by itself, give you a populated accessibility
tree. The tree comes from the operating system, and the OS must be configured to
expose it. The steps differ per platform.

You can check which backends report themselves runnable at any time:

```python
from cerebellum_cua.capture import available_backends
print(available_backends())   # e.g. [] or ['atspi'] or ['uia']
```

An empty list means no backend can run here. A backend appearing in the list
means its libraries are importable and (for AT-SPI) the a11y bus probe succeeded;
it does **not** guarantee that any given application exposes a usable tree.

### Linux / AT-SPI

Live capture on Linux reads the AT-SPI2 tree. Two things must be true: the AT-SPI
accessibility bus (`org.a11y.Bus`) must be running, and the applications you want
to capture must actually expose accessibility trees.

1. Install the AT-SPI core and the GObject-Introspection bindings. On
   Debian/Ubuntu these are typically `at-spi2-core`, `gir1.2-atspi-2.0`, and
   `python3-gi`; on Fedora-family systems `at-spi2-core` and `python3-gobject`.
   The exact package names vary by distribution.

2. Enable the accessibility toolkit so applications publish their trees:

   ```bash
   gsettings set org.gnome.desktop.interface toolkit-accessibility true
   export QT_ACCESSIBILITY=1
   export GTK_MODULES=gail:atk-bridge
   ```

3. Ensure the a11y bus and registry are running. The bus is normally activated on
   demand by D-Bus. You can confirm it is reachable with:

   ```bash
   gdbus call --session --dest org.a11y.Bus --object-path /org/a11y/bus \
     --method org.a11y.Bus.GetAddress
   ```

   A `unix:...` address means the bus is up. This is the same probe the AT-SPI
   backend uses in `is_available()`.

4. **Start applications after the bus is up.** An application only exposes its
   tree if the accessibility bridge was active when it started. Apps launched
   before the bus was running, or apps that do not implement accessibility (many
   custom-drawn or legacy UIs), expose empty or near-empty trees. **An app with
   an empty tree yields an empty capture — there is no pixel fallback.**

#### Honest note for SELinux-enforcing immutable distributions

On SELinux-enforcing immutable distributions (for example Fedora Silverblue,
Bazzite, and Kinoite), D-Bus activation of `at-spi2-registryd` may be denied. You
will see errors such as `Permission denied` with an SELinux denial referencing
the `gnome_atspi_exec_t` label. In that situation the bus probe fails and the
AT-SPI backend reports itself unavailable. Working around this generally requires
one of:

- adding an SELinux policy allowance for the registry daemon to be D-Bus
  activated, or
- running the AT-SPI bus launcher and registry daemon yourself as a user service
  rather than relying on D-Bus activation.

This is an environment configuration issue outside the package; `cerebellum-cua`
cannot grant itself the access. As above, applications only expose trees if they
were started after the bus came up.

### Windows / UIA

Live capture on Windows reads the UI Automation tree.

```bash
pip install -e '.[uia]'
```

This requires Windows 10 or 11 and the `uiautomation` and `comtypes` packages
(installed by the extra). The auto backend selects UIA on Windows.

**The Windows / UIA path is unverified on real Windows in this project's own
testing.** The UIA-dependent code is exercised on Linux only with the COM layer
mocked. Treat the Windows path as untested against live applications until you
have validated it on your own Windows host.

## Verifying the install

```python
import cerebellum_cua
from cerebellum_cua.capture import available_backends

print(cerebellum_cua.__name__)      # cerebellum_cua
print(available_backends())          # backends runnable on this host
```

Importing the package succeeds on any supported OS regardless of which capture
backend (if any) is available, because the OS-specific libraries are imported
lazily only when a backend is actually used.
