# KAIST Study MCPB 0.3.11

Portable source bundle for a local read-only MCP server. No author-specific
paths, browser profiles, credentials, mail, database or downloads are included.

## Required setup on EACH PC

1. Install Python 3.12 or newer.
2. Extract the matching source ZIP, open its directory in a terminal and run
   `py -3.12 install.py` (Windows) or `python3 install.py` (macOS/Linux).
   Alternatively extract this MCPB as a ZIP and run its included `install.py`.
3. The installer creates a virtual environment, installs dependencies and
   Chromium, checks browser launch and runs a mock stdio diagnostic in an
   isolated temporary data directory. Internet access is required.
4. Install this MCPB in a compatible desktop client. In **Installed Python
   executable**, enter the absolute Python executable path printed by the
   installer. Keep that virtual environment in its original location.
5. Restart the client, call `login_start` and authenticate with YOUR KAIST
   account. Open KLMS and Dooray from the portal; use `login_status` and
   `login_finish` to complete login. Never share cookies or browser profiles.

The runtime is not bundled: installing the MCPB alone is insufficient.
Alternatively merge the generated `.install/claude-desktop-config.json`
fragment into a stdio MCP client's existing configuration; do not register
both methods at the same time. Existing client settings are never overwritten.

Windows installation is tested. macOS/Linux paths are supported but actual
installation there remains unverified; Linux additionally needs Chromium's
system libraries, a desktop for interactive login and a compatible MCP client.
Each OS user has a separate platformdirs data/profile directory. Users sharing
an OS account share its local session unless separate absolute data/profile
paths are configured. No `.env` file is automatically loaded.

Portal tools cover eight board categories and server-side search. Mail search
pages start at 0; portal board pages start at 1. Mail detail uses raw MIME to
preserve read status. Academic access permits only grades, lectures and
graduation, and is disabled until exact read-only screen URLs are verified.
Whole-course material retrieval can take over a minute; choose a course or
configure a sufficiently long client tool timeout. No write tools are exposed.

On update, rerun the installer from the matching version's source before
selecting that environment in extension settings. Do not distribute generated
local configuration, browser state, mail, downloaded files or logs.
