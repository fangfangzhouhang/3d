import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const firmwareDirectory = resolve(testDirectory, "..");
const repositoryDirectory = resolve(firmwareDirectory, "../../../..");
const gitignore = readFileSync(resolve(repositoryDirectory, ".gitignore"), "utf8");
const mainSource = readFileSync(resolve(firmwareDirectory, "Core/Src/main.c"), "utf8");
const receiverHeader = readFileSync(resolve(firmwareDirectory, "app/include/line_receiver.h"), "utf8");

for (const relativePath of [
  "f401re-stage1.ioc",
  "Core/Src/main.c",
  "app/src/mcv1_protocol.c",
  "app/include/mcv1_protocol.h",
  "README.md",
]) {
  assert.ok(existsSync(resolve(firmwareDirectory, relativePath)), `${relativePath} must be versioned`);
}

assert.match(mainSource, /#include "mcv1_protocol\.h"/);
assert.match(mainSource, /mcv1_process_line\(/);
assert.match(mainSource, /mcv1_step\(/);
assert.match(receiverHeader, /FW_LINE_CAPACITY 128u/);

for (const ignoredPattern of [
  "MicroCleaningVision/firmware/nucleo_f401re/**/Debug/",
  "MicroCleaningVision/firmware/nucleo_f401re/**/Release/",
  "MicroCleaningVision/firmware/nucleo_f401re/**/*.elf",
  "MicroCleaningVision/firmware/nucleo_f401re/**/*.hex",
  "MicroCleaningVision/firmware/nucleo_f401re/**/*.bin",
]) {
  assert.ok(gitignore.includes(ignoredPattern), `${ignoredPattern} must be ignored`);
}

console.log("repository firmware layout: passed");
