# SPDX-License-Identifier: GPL-3.0-or-later
"""Registers (or removes) the Riffroom MCP server in Claude Code and in Claude Desktop, when they are installed.
Only the "riffroom" entry is touched; the Claude Desktop configuration is backed up first.

Usage: python -I -X utf8 setup_claude.py register|unregister"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, 'mcp_server.py')
NAME = 'riffroom'


def _claude_code(register):
    exe = shutil.which('claude')
    if not exe:
        return 'Claude Code: not installed'
    subprocess.run([exe, 'mcp', 'remove', NAME, '--scope', 'user'], capture_output=True, text=True, timeout=60)
    if not register:
        return 'Claude Code: removed'
    r = subprocess.run([exe, 'mcp', 'add', NAME, '--scope', 'user', '--', sys.executable, '-I', '-X', 'utf8', SERVER],
                       capture_output=True, text=True, timeout=60)
    return 'Claude Code: registered' if r.returncode == 0 else f'Claude Code: failed ({(r.stderr or r.stdout).strip()[:200]})'


def _claude_desktop(register):
    folder = os.path.join(os.environ.get('APPDATA', ''), 'Claude')
    if not os.path.isdir(folder):
        return 'Claude Desktop: not installed'
    path = os.path.join(folder, 'claude_desktop_config.json')
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
        cfg = json.loads(text) if text.strip() else {}
        backup = path + '.before-riffroom'
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
    servers = cfg.setdefault('mcpServers', {})
    if register:
        servers[NAME] = {'command': sys.executable, 'args': ['-I', '-X', 'utf8', SERVER]}
    else:
        servers.pop(NAME, None)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return 'Claude Desktop: ' + ('registered (restart Claude Desktop to see it)' if register else 'removed')


def main():
    register = (sys.argv[1:] or ['register'])[0] != 'unregister'
    for fn in (_claude_code, _claude_desktop):
        try:
            print('  ' + fn(register))
        except Exception as e:
            print(f'  {fn.__name__}: error {e}')


if __name__ == '__main__':
    main()
