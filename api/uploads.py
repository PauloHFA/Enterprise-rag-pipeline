"""Bound uploads before parsing multipart and validate content before persistence."""
import io
import re
import zipfile
from tempfile import SpooledTemporaryFile
from pathlib import PurePosixPath
from fastapi import HTTPException
from starlette.responses import JSONResponse
from .config import settings


class BodyTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        maximum = settings.MAX_UPLOAD_BYTES + settings.MAX_MULTIPART_OVERHEAD_BYTES
        headers = dict(scope.get('headers', []))
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, 400)(scope, receive, send)
        if declared < 0 or declared > maximum:
            return await JSONResponse({'detail': 'Request body too large'}, 413)(scope, receive, send)
        # Bound the stream before the multipart parser can catch/convert exceptions.
        with SpooledTemporaryFile(max_size=1024 * 1024) as spool:
            total = 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                data = message.get('body', b'')
                total += len(data)
                if total > maximum:
                    return await JSONResponse({'detail': 'Request body too large'}, 413)(scope, receive, send)
                spool.write(data)
                if not message.get('more_body', False):
                    break
            spool.seek(0)
            exhausted = False
            async def limited_receive():
                nonlocal exhausted
                if exhausted:
                    return await receive()
                data = spool.read(64 * 1024)
                exhausted = spool.tell() == total
                return {'type': 'http.request', 'body': data, 'more_body': not exhausted}
            await self.app(scope, limited_receive, send)


MIMES = {'.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.txt': 'text/plain', '.md': 'text/markdown', '.markdown': 'text/markdown',
         '.html': 'text/html', '.htm': 'text/html'}


def validate_upload(content, filename, declared_type):
    name = PurePosixPath((filename or '').replace('\\', '/')).name
    if not name or len(name) > 240 or any(ord(char) < 32 for char in name):
        raise HTTPException(400, 'Invalid filename')
    extension = PurePosixPath(name.lower()).suffix
    expected = MIMES.get(extension)
    if not expected:
        raise HTTPException(415, 'Unsupported file extension')
    if not content:
        raise HTTPException(400, 'Empty file')
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'File too large')
    detected = None
    if content.startswith(b'%PDF-'):
        detected = MIMES['.pdf']
    elif content.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > settings.MAX_DOCX_EXPANDED_BYTES:
                    raise HTTPException(413, 'Expanded DOCX too large')
                if any(item.flag_bits & 1 or item.filename.lower().endswith(('vbaproject.bin', '.exe')) for item in entries):
                    raise HTTPException(415, 'Encrypted or executable archive content')
                if {'[Content_Types].xml', 'word/document.xml'}.issubset(archive.namelist()):
                    detected = MIMES['.docx']
        except zipfile.BadZipFile as exc:
            raise HTTPException(415, 'Invalid DOCX archive') from exc
    else:
        try:
            value = content.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise HTTPException(415, 'Text must be UTF-8') from exc
        if any(ord(char) < 32 and char not in '\n\r\t\f' for char in value):
            raise HTTPException(415, 'Binary content is not text')
        is_html = bool(re.search(r'<(?:!doctype\s+html|html|head|body|title|h[1-6]|p|div|script|table)\b', value, re.I))
        detected = 'text/html' if is_html else ('text/markdown' if extension in ('.md', '.markdown') else 'text/plain')
    if detected != expected:
        raise HTTPException(415, 'File content does not match extension')
    declared = (declared_type or '').split(';')[0].lower()
    if declared not in ('', 'application/octet-stream', expected):
        raise HTTPException(415, 'Content-Type does not match file content')
    return name, detected
