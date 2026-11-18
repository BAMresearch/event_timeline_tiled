# Questions for IT

What the timeline needs to know, or to have set up, before it runs in the
lab. Open questions first; move each answer, with its date, to
[Answered](#answered).

## Lab Tiled server

1. **Upgrade.** The server runs Tiled 0.2.7; the timeline is tested on
   0.2.21. Can it be upgraded to 0.2.21, and when? The upgrade includes
   catalogue database migrations (access tags since 0.2.19, a new metadata
   index), so it needs a backup of the catalogue first and a short downtime.
   Instruments write with Tiled 0.2.8 clients; 0.2.21 accepts them (checked
   on the demo stack, plan §10.2).
2. **Multi-user mode.** The server runs in single-user mode: its one API key
   can write. The timeline must read only. Can it run in multi-user mode,
   with an authentication provider, and give the timeline its own API key
   with only the `read:metadata` and `read:data` scopes? (How it is checked:
   [Tiled access](tiled-access.md).)
3. **Writers' accounts.** In multi-user mode the instruments' writers need
   accounts too, and MoDaCor's publisher needs the `register` scope
   (by default only the `admin` role has it; decision D-003).
4. **Storage.** Which database holds the catalogue (SQLite or PostgreSQL),
   and which holds the event tables (DuckDB, SQLite or PostgreSQL)? With
   DuckDB tables, Tiled 0.2.21 fails some reads made at the same time
   ([testing](testing.md)); PostgreSQL is what the timeline is tested on.
5. **MoDaCor's output.** The directory where MoDaCor writes processed data
   must be mounted on the Tiled server at the same path and listed as
   readable storage there (processing-runs.md §1.1).
6. **Network.** The plan assumes the server is reachable only from the lab
   network, but it answered over HTTPS from outside. Which is intended?
7. **Service account.** The Tiled containers run rootless under an
   administrator's personal account (found 2026-11-18). They stop if that
   account is disabled or logs out without lingering, and their data and
   configuration live in that home directory. Can they move to a dedicated
   service account, started as systemd user services with lingering
   enabled? The upgrade (1) and the switch to multi-user mode (2) could be
   done in the same step. Where are the catalogue and the data backed up?

## The machine that runs the dashboard

The stack is a set of containers (Grafana, a proxy, the adapter), so far run
with Docker; the lab uses Podman. The commands below answer questions 8–11;
run them as the user who will run the stack, without `sudo` (rootless
Podman is per user):

```sh
cat /etc/os-release              # 8: distribution and version
uname -r                         #    kernel
podman --version                 # 9: Podman version
podman info --format 'rootless={{.Host.Security.Rootless}} selinux={{.Host.Security.SELinuxEnabled}} network={{.Host.NetworkBackend}} cgroups={{.Host.CgroupsVersion}}'
podman compose version           # 10: which compose provider Podman uses
command -v podman-compose docker-compose
getenforce                       # 11: SELinux mode (command missing: no SELinux)
loginctl show-user "$USER" -p Linger   # can the user's containers run after logout?
```

8. *(answered below)*
9. **Podman upgrade.** The machine has Podman 4.6.2 from the openSUSE Build
   Service, built for Ubuntu 22.04 (`4:4.6.2-0ubuntu22.04+obs81.12`), whose
   repository is no longer configured (left from the upgrade to 24.04?).
   Its epoch (`4:`) ranks it above Ubuntu's 4.9.3, so apt never replaces
   it. Please replace it, and the container packages that came with it
   (`apt list --installed 2>/dev/null | grep obs` lists them: typically
   conmon, crun, netavark, aardvark-dns, containers-common), by Ubuntu
   24.04's packages, for example
   `sudo apt install --allow-downgrades podman=4.9.3+ds1-1ubuntu0.2`
   with the matching versions of the others. Podman 4.7 and later also
   have `podman compose`. Also: `apt policy` lists a `resolute` (Ubuntu
   26.04) source at priority 50 on this 24.04 machine; is that intended?
10. **Compose.** Podman 4.6 has no `podman compose` (added in 4.7.0, and even
   then only a wrapper around another tool). Proposed: install Ubuntu's
   `docker-compose-v2` (2.24.6; the compose tool only, not Docker) and use
   it with Podman's Docker-compatible socket:
   `systemctl --user enable --now podman.socket`, then
   `DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock`. The
   alternative, Ubuntu's `podman-compose` 1.0.6, is untested with the
   stack's health-check conditions.
11. *(answered below)*
12. Which machine runs it: the dashboard may run on its own server. From
    there it needs HTTPS to the Tiled server and HTTP to the MOUSE RE
    Manager's read-only HTTP server
    ([brief](../design/upstream/brief-mouse-bluesky-queue-http.md)).
13. Starting at boot: as a systemd service (rootless needs lingering, the
    last command above), or another way? The stack should run under a
    service account too, not a personal one.

## Access for operators

14. **Address and TLS.** Under which host name do operators open it, and who
    provides the certificate? A TLS terminator goes in front of the proxy;
    Grafana then needs that public URL and secure cookies (plan §10.2).
15. **Accounts.** Operators sign in with Grafana Viewer accounts (anonymous
    access is off). Local Grafana accounts, or sign-in through the
    institute's directory (LDAP or OpenID Connect)?

## Answered

From the dashboard machine, 2026-11-18:

- **8.** Ubuntu 24.04.5 LTS, kernel 6.8.0-134-generic.
- **9.** Podman 4.6.2 from the openSUSE Build Service for Ubuntu 22.04,
  installed with an epoch above Ubuntu's 4.9.3 (`apt policy podman`,
  2026-11-18); its repository is gone. Upgrade asked for in question 9.
- **10.** `podman compose` does not exist (Podman 4.6); no provider chosen
  yet (above).
- **11.** Rootless; SELinux not enabled (Ubuntu uses AppArmor); network
  backend netavark, so containers resolve names through aardvark-dns at the
  network's gateway, not at Docker's `127.0.0.11`. The cgroups field is
  not reported by this Podman; Ubuntu 24.04 uses cgroups v2.
- **13.** Lingering is off (`Linger=no`) for both the administrator's
  account that runs Tiled and the maintainer's account: containers of
  either stop when the user's last session ends. A service account with
  lingering on is asked for in questions 7 and 13.
