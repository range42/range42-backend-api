"""Bounded optional transport to an installed, unprivileged identity dispatcher."""
import asyncio
import json
import os
from pathlib import Path
import stat

from app.core.errors import Range42Error

LIMIT = 2 * 1024 * 1024


def unavailable():
    return Range42Error(status=503, code='IDENTITY_RUNTIME_UNAVAILABLE',
        message='The identity integration is unavailable. Configure the platform identity runtime, then refresh before making changes.')


def unique_keys(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate protocol field')
        result[name] = value
    return result


def decode_result(raw):
    try:
        value = json.loads(raw, object_pairs_hook=unique_keys)
        if not isinstance(value, dict) or type(value.get('ok')) is not bool:
            raise ValueError()
        if value['ok'] is True and isinstance(value.get('result'), dict):
            return value['result']
        code = value.get('code')
        if code == 'IDENTITY_REVIEW_CONFLICT':
            raise Range42Error(status=409, code=code,
                message='Identity state or retained ownership changed. Refresh and review before submitting again.')
        if code == 'IDENTITY_SEAT_OCCUPIED':
            raise Range42Error(status=409, code=code, message='This participant seat has no remaining places. Choose another seat or remove its current membership.')
        if code == 'IDENTITY_REQUEST_INVALID':
            raise Range42Error(status=422, code=code, message='The installed identity runtime refused this desired change.')
    except (ValueError, TypeError, UnicodeError):
        pass
    raise unavailable()


async def identity_request(value):
    config = os.environ.get('RANGE42_IDENTITY_SSH_CONFIG')
    process = None
    try:
        if not config:
            raise ValueError()
        path = Path(config)
        info = path.lstat()
        if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or info.st_uid not in {0, os.geteuid()} or info.st_nlink != 1):
            raise ValueError()
        raw = json.dumps(value).encode()
        if len(raw) > 16384:
            raise ValueError()
        process = await asyncio.create_subprocess_exec(
            'ssh', '-F', str(path), '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
            '-o', 'StrictHostKeyChecking=yes', 'range42-identities', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        async with asyncio.timeout(15):
            process.stdin.write(raw)
            await process.stdin.drain()
            process.stdin.close()
            response = await process.stdout.read(LIMIT + 1)
            # StreamReader.read(n) may return a short chunk before EOF.
            while len(response) <= LIMIT:
                chunk = await process.stdout.read(min(65536, LIMIT + 1 - len(response)))
                if not chunk:
                    break
                response += chunk
            if len(response) > LIMIT:
                raise ValueError()
            code = await process.wait()
        if code not in {0, 1}:
            raise ValueError()
        return decode_result(response)
    except (TimeoutError, OSError, ValueError, RuntimeError):
        raise unavailable() from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
