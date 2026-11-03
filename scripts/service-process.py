#!/usr/bin/env python3
"""Inspect a device service without exposing process environments or killing PIDs."""
import argparse
from pathlib import Path
import shlex
import subprocess


def matches_service(command, executable, module):
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    # Launchers use this exact virtualenv and -u -m. Do not accept a substring
    # in another program's arguments, another checkout, or the other module.
    resolved = Path(executable).resolve()
    aliases = {str(executable), str(resolved)}
    framework = resolved.parent.parent / 'Resources/Python.app/Contents/MacOS/Python'
    if framework.is_file():
        aliases.add(str(framework))
    return bool(words and words[0] in aliases and words[1:] in (['-u', '-m', module], ['-m', module]))


def inspect_service(pidfile, executable, module, port, run=subprocess.run):
    def output(args):
        result = run(args, capture_output=True, text=True, check=False, timeout=5)
        if result.returncode not in (0, 1) or result.stderr.strip():
            raise RuntimeError('Process ownership could not be verified')
        return result.stdout.strip()
    raw = Path(pidfile).read_text().strip() if Path(pidfile).exists() else ''
    pid = int(raw) if raw.isdecimal() and int(raw) > 1 else None
    owners = output(['/usr/sbin/lsof', '-nP', '-t', '-iTCP:'+str(port), '-sTCP:LISTEN']).splitlines()
    owners = set(owners)
    def owned_process():
        command = output(['/bin/ps', '-p', str(pid), '-o', 'command='])
        if not matches_service(command, executable, module):
            return False
        cwd = output(['/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'])
        checkout = Path(executable).absolute().parents[2]
        return ('n'+str(checkout)) in cwd.splitlines()
    if owners:
        # A listener is stronger evidence than an old PID file. Never start a
        # duplicate or overwrite ownership when either check disagrees.
        if pid is None or owners != {str(pid)}:
            return 'conflict'
        return 'running' if owned_process() else 'conflict'
    if pid is not None:
        if owned_process():
            return 'starting_or_unhealthy'
    return 'absent'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('pidfile'); p.add_argument('executable'); p.add_argument('module')
    p.add_argument('port', type=int)
    a = p.parse_args()
    try:
        state = inspect_service(a.pidfile, a.executable, a.module, a.port)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        state = 'unverified'
    print(state)
    return 0 if state == 'running' else 3 if state == 'absent' else 4


if __name__ == '__main__':
    raise SystemExit(main())
