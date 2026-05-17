#!/usr/bin/env node
/**
 * npm/npx wrapper for Codeward.
 *
 * Codeward itself is a Python package — this wrapper makes `npx codeward ...`
 * and `npm i -g codeward` work for users who don't want to think about pip/pipx.
 *
 * On first run:
 *   1. Detect an existing `codeward` on PATH; if found, just exec it.
 *   2. Otherwise, install it once with `pipx install codeward` (preferred —
 *      isolated, on PATH) or `pip install --user codeward` (fallback).
 *   3. Then exec the freshly-installed binary.
 *
 * All install output is forwarded to stderr so it never pollutes piped JSON.
 */
"use strict";

const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const PKG = "codeward";
const MIN_PY = [3, 11];

function which(cmd) {
  const r = spawnSync(process.platform === "win32" ? "where" : "which", [cmd], {
    encoding: "utf8",
  });
  if (r.status !== 0) return null;
  const first = (r.stdout || "").split(/\r?\n/).find(Boolean);
  return first ? first.trim() : null;
}

function pythonVersionOk(py) {
  const r = spawnSync(py, ["-c", "import sys; print(sys.version_info[0], sys.version_info[1])"], {
    encoding: "utf8",
  });
  if (r.status !== 0) return false;
  const [maj, min] = r.stdout.trim().split(/\s+/).map(Number);
  if (Number.isNaN(maj) || Number.isNaN(min)) return false;
  return maj > MIN_PY[0] || (maj === MIN_PY[0] && min >= MIN_PY[1]);
}

function findPython() {
  for (const cand of ["python3.13", "python3.12", "python3.11", "python3", "python"]) {
    const p = which(cand);
    if (p && pythonVersionOk(p)) return p;
  }
  return null;
}

function installCodeward(py) {
  const pipx = which("pipx");
  const log = (msg) => process.stderr.write(`[codeward] ${msg}\n`);

  if (pipx) {
    log(`Installing ${PKG} via pipx (one-time, isolated)…`);
    const r = spawnSync(pipx, ["install", PKG], { stdio: "inherit" });
    if (r.status === 0) return true;
    log("pipx install failed; falling back to pip --user.");
  }

  log(`Installing ${PKG} via pip --user (one-time)…`);
  const r = spawnSync(py, ["-m", "pip", "install", "--user", PKG], {
    stdio: "inherit",
  });
  return r.status === 0;
}

function execCodeward(bin, args) {
  const r = spawnSync(bin, args, { stdio: "inherit" });
  process.exit(r.status == null ? 1 : r.status);
}

function pyUserBin(py) {
  const r = spawnSync(py, ["-c", "import site, os, sys; print(os.path.join(site.USER_BASE, 'Scripts' if os.name=='nt' else 'bin'))"], { encoding: "utf8" });
  return r.status === 0 ? r.stdout.trim() : null;
}

function main() {
  const args = process.argv.slice(2);

  // Already on PATH? Just exec it. Common after install or for users who
  // installed via pipx/pip directly and only `npx codeward` once.
  const existing = which(PKG);
  if (existing) {
    execCodeward(existing, args);
    return;
  }

  const py = findPython();
  if (!py) {
    process.stderr.write(
      `Codeward needs Python ${MIN_PY[0]}.${MIN_PY[1]}+ on PATH (couldn't find one).\n` +
        "Install Python from https://python.org, then re-run.\n",
    );
    process.exit(127);
  }

  if (!installCodeward(py)) {
    process.stderr.write(
      "Codeward install failed. Try manually:  pipx install codeward\n",
    );
    process.exit(1);
  }

  // After install, the binary may live in pipx's bin dir or pip's --user
  // scripts dir. Re-check PATH first; otherwise probe the user scripts dir.
  const fresh = which(PKG) || (function () {
    const userBin = pyUserBin(py);
    if (!userBin) return null;
    const guess = path.join(userBin, process.platform === "win32" ? "codeward.exe" : "codeward");
    return fs.existsSync(guess) ? guess : null;
  })();

  if (!fresh) {
    process.stderr.write(
      "Installed Codeward, but couldn't find the binary on PATH.\n" +
        "Add your pipx/user bin directory to PATH and retry.\n",
    );
    process.exit(1);
  }
  execCodeward(fresh, args);
}

main();
