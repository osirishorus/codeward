from __future__ import annotations

import json
import os
import selectors
import shlex
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse


SERVER_CANDIDATES = {
    "python": [["pyright-langserver", "--stdio"], ["basedpyright-langserver", "--stdio"], ["pylsp"]],
    "typescript": [["typescript-language-server", "--stdio"]],
    "javascript": [["typescript-language-server", "--stdio"]],
    "go": [["gopls"]],
    "rust": [["rust-analyzer"]],
}

INSTALL_HINTS = {
    "python": "install pyright-langserver",
    "typescript": "install typescript-language-server",
    "javascript": "install typescript-language-server",
    "go": "install gopls",
    "rust": "install rust-analyzer",
}


class LspUnavailable(Exception):
    """Raised when LSP precision cannot be used for this query."""


def normalize_language(language: str) -> str:
    key = language.strip().lower()
    return {
        "python": "python",
        "py": "python",
        "typescript": "typescript",
        "ts": "typescript",
        "javascript": "javascript",
        "js": "javascript",
        "go": "go",
        "golang": "go",
        "rust": "rust",
        "rs": "rust",
    }.get(key, key)


def detect_servers() -> dict[str, list[list[str]]]:
    detected: dict[str, list[list[str]]] = {}
    for language, candidates in SERVER_CANDIDATES.items():
        override = os.environ.get(f"CODEWARD_LSP_SERVER_{language.upper()}")
        commands = [shlex.split(override)] if override and override.strip() else candidates
        available = [cmd for cmd in commands if cmd and shutil.which(cmd[0])]
        if available:
            detected[language] = available
    return detected


def server_for(language: str) -> list[str] | None:
    return (detect_servers().get(normalize_language(language)) or [None])[0]


def _encode_message(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _decode_message(raw: bytes) -> dict:
    header, body = raw.split(b"\r\n\r\n", 1)
    length = None
    for line in header.decode("ascii").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            break
    if length is None:
        raise LspUnavailable("missing Content-Length")
    return json.loads(body[:length].decode("utf-8"))


def str_column_to_lsp_character(line: str, column: int) -> int:
    column = max(0, min(column, len(line)))
    return len(line[:column].encode("utf-16-le")) // 2


def lsp_character_to_column(line: str, character: int) -> int:
    if character <= 0:
        return 0
    units = 0
    for i, ch in enumerate(line):
        next_units = units + (len(ch.encode("utf-16-le")) // 2)
        if next_units > character:
            return i
        units = next_units
        if units == character:
            return i + 1
    return len(line)


def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise LspUnavailable(f"unsupported URI scheme: {parsed.scheme}")
    return Path(unquote(parsed.path))


def position_for_symbol(path: Path, line: int, symbol: str) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if line < 1 or line > len(lines):
        raise LspUnavailable("definition line outside file")
    text = lines[line - 1]
    bare = symbol.rsplit(".", 1)[-1]
    column = text.find(bare)
    if column < 0:
        column = max(0, len(text) - len(text.lstrip()))
    return line - 1, str_column_to_lsp_character(text, column)


def _timeout() -> float:
    try:
        return float(os.environ.get("CODEWARD_LSP_TIMEOUT", "10"))
    except ValueError:
        return 10.0


class LspSession:
    def __init__(self, root: Path, language: str, file: Path, command: list[str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.language = normalize_language(language)
        self.file = Path(file).resolve()
        self.command = command or server_for(self.language)
        self.process: subprocess.Popen | None = None
        self._next_id = 1
        self.server_name = self.command[0] if self.command else ""

    def __enter__(self) -> "LspSession":
        if not self.command:
            hint = INSTALL_HINTS.get(self.language, "install a language server")
            raise LspUnavailable(f"no server for {self.language} ({hint})")
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            self._request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": path_to_uri(self.root),
                    "capabilities": {},
                },
                timeout=float(os.environ.get("CODEWARD_LSP_TIMEOUT", "15")),
            )
            self._notify("initialized", {})
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": path_to_uri(self.file),
                        "languageId": self.language,
                        "version": 1,
                        "text": self.file.read_text(encoding="utf-8", errors="replace"),
                    }
                },
            )
            return self
        except Exception as e:
            self.close(kill=True)
            if isinstance(e, LspUnavailable):
                raise
            raise LspUnavailable(str(e)) from e

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(kill=False)

    def definition(self, line: int, character: int) -> list[dict]:
        return self._location_request("textDocument/definition", line, character)

    def references(self, line: int, character: int) -> list[dict]:
        return self._location_request(
            "textDocument/references",
            line,
            character,
            extra={"context": {"includeDeclaration": True}},
        )

    def close(self, *, kill: bool = False) -> None:
        proc = self.process
        if not proc:
            return
        if proc.poll() is None and not kill:
            try:
                self._request("shutdown", None, timeout=1.0)
                self._notify("exit", {})
            except LspUnavailable:
                kill = True
        if proc.poll() is None:
            if kill:
                proc.kill()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.process = None

    def _location_request(self, method: str, line: int, character: int, extra: dict | None = None) -> list[dict]:
        params = {
            "textDocument": {"uri": path_to_uri(self.file)},
            "position": {"line": line, "character": character},
        }
        if extra:
            params.update(extra)
        result = self._request(method, params, timeout=_timeout())
        if result is None:
            return []
        if isinstance(result, dict):
            return [result]
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params, *, timeout: float) -> object:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            response = self._read(timeout)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise LspUnavailable(str(response["error"]))
            return response.get("result")

    def _write(self, message: dict) -> None:
        proc = self.process
        if not proc or not proc.stdin or proc.poll() is not None:
            raise LspUnavailable("language server is not running")
        try:
            proc.stdin.write(_encode_message(message))
            proc.stdin.flush()
        except OSError as e:
            raise LspUnavailable(str(e)) from e

    def _read(self, timeout: float) -> dict:
        proc = self.process
        if not proc or not proc.stdout:
            raise LspUnavailable("language server is not running")
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            if not selector.select(timeout):
                raise LspUnavailable("language server timed out")
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = proc.stdout.read(1)
                if not chunk:
                    raise LspUnavailable("language server exited")
                header += chunk
            length = None
            for line in header.decode("ascii", errors="replace").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
                    break
            if length is None:
                raise LspUnavailable("missing Content-Length")
            body = proc.stdout.read(length)
            if len(body) != length:
                raise LspUnavailable("language server exited")
            return json.loads(body.decode("utf-8"))
        finally:
            selector.close()
