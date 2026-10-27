"""Serve docs/ locally with HTTP byte-range support for PMTiles."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """A static-file handler with single-range GET/HEAD support and CORS."""

    server_version = "WatershedDocs/1.0"

    def __init__(self, *args, directory: str | None = None, **kwargs):
        self._range_length: int | None = None
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range")
        super().end_headers()

    def send_head(self) -> BinaryIO | None:
        self._range_length = None
        path = Path(self.translate_path(self.path)).resolve()
        root = Path(self.directory or Path.cwd()).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            self.send_error(404, "File not found")
            return None

        if path.is_dir():
            return super().send_head()
        if not path.is_file():
            self.send_error(404, "File not found")
            return None

        file = path.open("rb")
        file_size = path.stat().st_size
        range_header = self.headers.get("Range")
        if range_header is None:
            self.send_response(200)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.end_headers()
            return file

        if not range_header.startswith("bytes=") or "," in range_header:
            file.close()
            return self._send_range_error(file_size)
        start_text, separator, end_text = range_header[6:].partition("-")
        if not separator or file_size == 0:
            file.close()
            return self._send_range_error(file_size)
        try:
            if start_text:
                start = int(start_text)
                end = int(end_text) if end_text else file_size - 1
            else:
                suffix_length = int(end_text)
                if suffix_length <= 0:
                    raise ValueError("Invalid byte-range suffix.")
                start = max(file_size - suffix_length, 0)
                end = file_size - 1
        except ValueError:
            file.close()
            return self._send_range_error(file_size)
        if start < 0 or end < start or start >= file_size:
            file.close()
            return self._send_range_error(file_size)
        end = min(end, file_size - 1)
        self._range_length = end - start + 1
        file.seek(start)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(self._range_length))
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.end_headers()
        return file

    def _send_range_error(self, file_size: int) -> None:
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{file_size}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def copyfile(self, source: BinaryIO, output) -> None:
        if self._range_length is None:
            super().copyfile(source, output)
            return
        remaining = self._range_length
        while remaining:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            output.write(chunk)
            remaining -= len(chunk)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs",
    )
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_dir():
        parser.error(f"Docs directory does not exist: {directory}")
    handler = lambda *handler_args, **handler_kwargs: RangeRequestHandler(
        *handler_args, directory=str(directory), **handler_kwargs
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {directory} at http://{args.host}:{args.port}/ (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
