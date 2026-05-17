import asyncio
import io
import socket
import threading
from dataclasses import dataclass

import httpx
import zstandard
from anki.collection import Collection
from anki.sync import SyncAuth


@dataclass
class SyncResult:
    success: bool
    message: str


class AnkiSyncer:
    def __init__(self, collection_path: str, email: str, password: str):
        self._collection_path = collection_path
        self._email = email
        self._password = password

    def sync(self) -> SyncResult:
        """Synchronous. Call via run_in_executor."""
        col = Collection(self._collection_path)
        try:
            auth: SyncAuth = col.sync_login(self._email, self._password, None)
            out = col.sync_collection(auth, sync_media=False)
            required = out.required
            if required in (out.NO_CHANGES, out.NORMAL_SYNC):
                return SyncResult(success=True, message="Sync complete")
            if required == out.FULL_DOWNLOAD:
                new_endpoint = _extract_new_endpoint(out)
                col.close_for_full_sync()
                _full_download_via_proxy(col, auth, new_endpoint)
                col.reopen(after_full_sync=True)
                return SyncResult(success=True, message="Full download complete")
            if required == out.FULL_UPLOAD:
                col.close_for_full_sync()
                col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
                col.reopen(after_full_sync=True)
                return SyncResult(success=True, message="Full upload complete")
            if required == out.FULL_SYNC:
                return SyncResult(
                    success=False,
                    message=(
                        "Full sync required: open Anki Desktop once to resolve "
                        "the conflict, or choose upload/download manually."
                    ),
                )
            return SyncResult(
                success=True, message=f"Sync complete (required={required})"
            )
        except Exception as e:
            return SyncResult(success=False, message=str(e))
        finally:
            try:
                col.close()
            except Exception:
                pass

    async def async_sync(self) -> SyncResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.sync)


def _extract_new_endpoint(sync_response) -> str:
    for fd, val in sync_response.ListFields():
        if fd.name == "new_endpoint":
            return val
    return "https://sync.ankiweb.net/"


def _read_chunked(rfile: io.RawIOBase) -> bytes:
    buf = io.BytesIO()
    while True:
        line = rfile.readline().strip()
        if not line:
            break
        size = int(line, 16)
        if size == 0:
            rfile.readline()
            break
        buf.write(rfile.read(size))
        rfile.readline()
    return buf.getvalue()


def _fix_zstd_content_size(data: bytes) -> bytes:
    """Re-compress zstd data with content size embedded (Rust backend requires it)."""
    buf = io.BytesIO()
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(data)) as reader:
        while True:
            chunk = reader.read(65536)
            if not chunk:
                break
            buf.write(chunk)
    cctx = zstandard.ZstdCompressor(level=1, write_content_size=True)
    return cctx.compress(buf.getvalue())


def _handle_proxy_conn(conn: socket.socket, real_endpoint: str) -> None:
    try:
        f = conn.makefile("rb")
        req_line = f.readline().decode()
        if not req_line.strip():
            return
        method, path, _ = req_line.split()
        headers: dict[str, str] = {}
        while True:
            line = f.readline().decode()
            if line in ("\r\n", "\n", ""):
                break
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

        if headers.get("transfer-encoding") == "chunked":
            body = _read_chunked(f)
        else:
            cl = int(headers.get("content-length", 0))
            body = f.read(cl) if cl else b""

        real_url = real_endpoint + path.lstrip("/")
        fwd_headers = {
            k: v
            for k, v in headers.items()
            if k not in ("host", "content-length", "transfer-encoding")
        }

        resp = httpx.post(real_url, content=body, headers=fwd_headers, timeout=120)
        content = resp.content

        if content[:4] == bytes.fromhex("28b52ffd"):
            content = _fix_zstd_content_size(content)

        resp_lines = [
            f"HTTP/1.1 {resp.status_code} OK\r\n",
            f"Content-Length: {len(content)}\r\n",
        ]
        for k, v in resp.headers.items():
            if k.lower() not in (
                "content-length",
                "transfer-encoding",
                "content-encoding",
                "connection",
            ):
                resp_lines.append(f"{k}: {v}\r\n")
        resp_lines.append("\r\n")
        conn.sendall("".join(resp_lines).encode() + content)
    except Exception:
        pass
    finally:
        conn.close()


def _full_download_via_proxy(
    col: Collection, auth: SyncAuth, real_endpoint: str
) -> None:
    """
    Workaround for anki Rust backend bug: full download response uses zstd without
    content size, which the Rust decompressor rejects. We run a local proxy that
    re-compresses the response with content size before returning it to the backend.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(5)
    srv.settimeout(180)

    def run_proxy():
        try:
            while True:
                try:
                    conn, _ = srv.accept()
                    threading.Thread(
                        target=_handle_proxy_conn,
                        args=(conn, real_endpoint),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    break
        finally:
            srv.close()

    proxy_thread = threading.Thread(target=run_proxy, daemon=True)
    proxy_thread.start()

    auth.endpoint = f"http://127.0.0.1:{port}/"
    col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
    srv.settimeout(0.1)
    proxy_thread.join(timeout=2)
