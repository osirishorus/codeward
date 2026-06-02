const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const Module = require("node:module");

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

  fs.mkdirSync(binDir);
  fs.writeFileSync(distinctBinary, "#!/bin/sh\nexit 0\n");
  fs.chmodSync(distinctBinary, 0o755);

  process.env.PATH = [binDir, originalPath].filter(Boolean).join(path.delimiter);
  process.exit = (code) => {
    process.exitCode = code;
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
    require(wrapperPath);
  } finally {
    Module._load = originalLoad;
    process.exit = originalExit;
    process.env.PATH = originalPath;
    delete require.cache[wrapperPath];
  }

  assert.equal(process.exitCode, 0);
  assert.ok(calls.some(({ command }) => command === distinctBinary));
  assert.ok(!calls.some(({ command }) => command === wrapperPath));
});
