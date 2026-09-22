# SSH Tunnels for Omarchy

A Quickshell bar plugin for finding SSH aliases, saving local port forwards,
starring favorites, and starting or stopping individual tunnels.

Requires Omarchy 4 with the Quickshell plugin host, Python 3, and OpenSSH.
There are no Python packages to install. Developed against Omarchy 4.0.4.

<img width="559" height="666" alt="image" src="https://github.com/user-attachments/assets/9c483c4d-3b79-44ec-8697-dd39d2c77b97" />


## Install

```sh
omarchy plugin add https://github.com/vleeuwenmenno/omasshtunnels.git --enable
```

## Install a local development copy

From this directory:

```sh
python3 install.py --enable
```

The installer validates the plugin, copies its runtime files to
`~/.config/omarchy/plugins/vleeuwenmenno.sshtunnels/`, rescans, and enables the bar widget.
An existing local installation is backed up before replacement. It never
changes SSH configuration or starts a tunnel.

## Use

1. Click the SSH Tunnels bar icon and select **SSH aliases**.
2. Search for an alias and click **Add tunnel**.
3. Enter a local port, a destination host, and a destination port. Optionally
   give it a name, then choose **Save & star**.
4. Click **Start** on the saved tunnel. Click **Stop** to close that tunnel.

For example, local port `15432`, destination `localhost`, and destination port
`5432` forwards `127.0.0.1:15432` on your computer to `localhost:5432` as seen
from the selected SSH server. `localhost` in the destination field refers to
the server, not your computer.

Stars persist across shell restarts. Starred tunnels sort first; **Starred**
and **Active** narrow the list. Search matches aliases, names, hosts, and ports.
Multiple saved tunnels can use the same SSH alias. Stop a tunnel before editing
or removing it. Removal requires a second click to confirm.

Saved tunnels and favorites live in
`${XDG_CONFIG_HOME:-~/.config}/omasshtunnels/tunnels.json`. They are independent
of the installed plugin directory, so plugin updates do not replace them.

The JSON file may be a symlink to a dotfiles checkout. Saves, edits, favorites,
and removals preserve that link and atomically update its target with mode
`0600`. A broken symlink is an error; restore its target before saving.

## Remove

Stop every active tunnel using its **Stop** button before removing the plugin.
Removing or disabling the widget does not terminate its SSH connections.

```sh
omarchy plugin remove vleeuwenmenno.sshtunnels
```

This removes the plugin and its bar entry. Saved tunnels and stars remain in
`${XDG_CONFIG_HOME:-~/.config}/omasshtunnels/tunnels.json`, so reinstalling restores
them. Your SSH configuration, keys, and unrelated SSH sessions are unchanged.

If the widget was removed while a tunnel was running, reinstall it to regain
its Stop button. The helper discovers its existing control sockets.

## SSH configuration

The plugin discovers literal `Host` aliases in `~/.ssh/config`, its `Include`
files, and `~/.ssh/config.d`. It also reads system SSH configuration. In
`config.d`, unreferenced `.conf` files and files without an extension are
included; editor backups and hidden files are ignored. Already included files
retain their original position and conditions. Unreferenced files are appended
after the main user config, before system defaults, without editing any source.

OpenSSH itself resolves aliases with `ssh -G`, preserving Host/Match rules,
identity files, ports, and ProxyJump. Wildcard-only Host patterns cannot be
listed as individual hosts; add a concrete alias for a host you want to select.
As with normal SSH use, your own configuration can contain executable
`Match exec`, proxy commands, or known-hosts helpers.

Aliases with effective `LocalForward`, `RemoteForward`, or `DynamicForward`
entries automatically appear in **Tunnels**. Starting one starts all its
configured forwards together. Saved local tunnels start only their own saved
forward, even if their alias also has forwards in SSH config. The first version
creates local forwards in the UI; remote and SOCKS forwards can be defined in
SSH config.

Status refreshes every five seconds. Config changes invalidate the discovery
cache; **Refresh** or right-clicking the bar icon forces full resolution.

## Authentication and lifecycle

- The plugin uses your existing SSH keys and agent. Connections are
  noninteractive: unlock a passphrase-protected key with `ssh-add` first.
  For a new host, verify its host key in a terminal using `ssh <alias>` before
  starting a tunnel. Password/MFA prompts are not supported in this version.
- Each tunnel has a dedicated control socket in your private runtime
  directory. Stop addresses that socket, never a PID or another session's
  configured `ControlPath`. Saved local listeners bind to `127.0.0.1`.
- Closing the popup, reloading the shell, disabling the widget, or updating
  the plugin does not stop active tunnels. Stop them before removing the plugin.
  Nothing is automatically started or reconnected.
- “Active” means the SSH master is alive and its forwarding setup succeeded.
  It does not prove that the destination application is reachable. Server
  keepalives detect dead SSH connections, normally within about 90 seconds.
- If an alias or forwarding entry disappears while a tunnel is running, the
  active tunnel remains listed so you can stop it.

## How the plugin works

| File | Purpose |
| --- | --- |
| `manifest.json` | Plugin identity, bar placement, and QML entry point |
| `BarWidget.qml` | Bar icon, active count, and popup lifecycle |
| `TunnelPanel.qml` | Search, saved tunnels, alias list, and forwarding form |
| `TunnelController.qml` | Asynchronous helper calls and status polling |
| `Model.js` | Search, sorting, filtering, and forwarding labels |
| `bin/tunnels.py` | SSH discovery, persistent favorites, and isolated tunnel control |

This is one `bar-widget`; its nested popup does not need a separate manifest
kind. It uses Omarchy's shared UI components and theme tokens. The helper runs
SSH with argument arrays rather than assembling shell command strings.

Edit source here and rerun `python3 install.py --enable` to install a new copy.
Omarchy hot-reloads installed QML. Force discovery with
`omarchy-shell shell rescanPlugins` if needed. Debug the running widget with
`qs log -p /usr/share/omarchy/shell --tail 100`.

## Verify

```sh
omarchy plugin validate .
python3 -m unittest discover -s tests -v
node tests/test_model.cjs
python3 tests/integration.py
```

The integration test requires `sshd` and permission to open loopback sockets.
It creates temporary keys, an SSH server, and an echo endpoint; checks actual
traffic, port-conflict handling, reload persistence, and session isolation;
then shuts them down. It never connects to your configured remote hosts.

`qmllint` needs a temporary import root containing `qs` pointing to the installed
`/usr/share/omarchy/shell` directory. The installed shell's dynamically typed
theme properties and Quickshell's `QProcess::ExitStatus` metadata produce lint
warnings; live loading remains part of verification.

References: [Omarchy shell contract](https://github.com/omacom/omarchy/blob/quattro/shell/README.md),
[plugin development guide](https://plugins.omarchy.org/develop.html), and
[OpenSSH client manual](https://man.openbsd.org/ssh).
