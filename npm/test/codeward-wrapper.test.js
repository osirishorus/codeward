const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const Module = require("node:module");

class ExitSignal extends Error {
  constructor(code) {
    super(`process.exit(${code})`);
    this.code = code;
  }
}

test("prefers a distinct codeward binary over the wrapper shim on PATH", () => {
  const wrapperPath = path.resolve(__dirname, "../bin/codeward.js");
  const originalPath = process.env.PATH;
  const originalExit = process.exit;
  const originalLoad = Module._load;
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "codeward-wrapper-"));
  const binDir = path.join(tmpDir, "bin");
  const distinctBinary = path.join(binDir, "codeward");
  const whichCmd = process.platform === "win32" ? "where" : "which";
  const calls = [];

  process.exitCode = undefined;
  fs.mkdirSync(binDir);
  fs.writeFileSync(distinctBinary, "#!/bin/sh\nexit 0\n");
  fs.chmodSync(distinctBinary, 0o755);

  process.env.PATH = [binDir, originalPath].filter(Boolean).join(path.delimiter);
  process.exit = (code) => {
    throw new ExitSignal(code);
  };

  Module._load = function mockedLoad(request, parent, isMain) {
    if (request === "node:child_process") {
      return {
        spawnSync(command, args) {
          calls.push({ command, args });

          if (command === whichCmd) {
            assert.deepEqual(args, ["codeward"]);
            return { status: 0, stdout: `${wrapperPath}\n`, stderr: "" };
          }

          if (command === wrapperPath) {
            throw new Error("wrapper shim must not be re-executed");
          }

          if (command === distinctBinary) {
            return { status: 0, stdout: "", stderr: "" };
          }

          throw new Error(`unexpected spawnSync call: ${command}`);
        },
      };
    }

    return originalLoad(request, parent, isMain);
  };

  delete require.cache[wrapperPath];

  try {
    assert.throws(() => require(wrapperPath), (err) => err instanceof ExitSignal && err.code === 0);
  } finally {
    Module._load = originalLoad;
    process.exit = originalExit;
    process.env.PATH = originalPath;
    delete require.cache[wrapperPath];
  }

  assert.ok(calls.some(({ command }) => command === distinctBinary));
  assert.ok(!calls.some(({ command }) => command === wrapperPath));
});

test("pins Python install to npm package version", () => {
  const wrapperPath = path.resolve(__dirname, "../bin/codeward.js");
  const originalPath = process.env.PATH;
  const originalExit = process.exit;
  const originalLoad = Module._load;
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "codeward-wrapper-"));
  const binDir = path.join(tmpDir, "bin");
  const freshBinary = path.join(binDir, "codeward");
  const whichCmd = process.platform === "win32" ? "where" : "which";
  const calls = [];
  let codewardLookups = 0;

  process.exitCode = undefined;
  fs.mkdirSync(binDir);
  fs.writeFileSync(freshBinary, "#!/bin/sh\nexit 0\n");
  fs.chmodSync(freshBinary, 0o755);
  process.env.PATH = tmpDir;
  process.exit = (code) => {
    throw new ExitSignal(code);
  };

  Module._load = function mockedLoad(request, parent, isMain) {
    if (request === "node:child_process") {
      return {
        spawnSync(command, args) {
          calls.push({ command, args });

          if (command === whichCmd) {
            const wanted = args[0];
            if (wanted === "codeward") {
              codewardLookups += 1;
              return codewardLookups === 1
                ? { status: 1, stdout: "", stderr: "" }
                : { status: 0, stdout: `${freshBinary}\n`, stderr: "" };
            }
            if (wanted === "python3.11") return { status: 0, stdout: "/usr/bin/python3.11\n", stderr: "" };
            if (wanted === "pipx") return { status: 0, stdout: "/usr/bin/pipx\n", stderr: "" };
            return { status: 1, stdout: "", stderr: "" };
          }

          if (command === "/usr/bin/python3.11") {
            return { status: 0, stdout: "3 11\n", stderr: "" };
          }

          if (command === "/usr/bin/pipx") {
            assert.deepEqual(args, ["install", "codeward==0.6.0"]);
            return { status: 0, stdout: "", stderr: "" };
          }

          if (command === freshBinary) {
            return { status: 0, stdout: "", stderr: "" };
          }

          throw new Error(`unexpected spawnSync call: ${command}`);
        },
      };
    }

    return originalLoad(request, parent, isMain);
  };

  delete require.cache[wrapperPath];

  try {
    assert.throws(() => require(wrapperPath), (err) => err instanceof ExitSignal && err.code === 0);
  } finally {
    Module._load = originalLoad;
    process.exit = originalExit;
    process.env.PATH = originalPath;
    delete require.cache[wrapperPath];
  }

  assert.ok(calls.some(({ command, args }) => command === "/usr/bin/pipx" && args[1] === "codeward==0.6.0"));
});
