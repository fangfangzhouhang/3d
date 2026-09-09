import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const firmwareDirectory = resolve(testDirectory, "..");
const projectDirectory = resolve(firmwareDirectory, "../../..");
const gitignore = readFileSync(resolve(projectDirectory, ".gitignore"), "utf8");
const mainSource = readFileSync(resolve(firmwareDirectory, "Core/Src/main.c"), "utf8");
const mainHeader = readFileSync(resolve(firmwareDirectory, "Core/Inc/main.h"), "utf8");
const receiverHeader = readFileSync(
  resolve(firmwareDirectory, "app/include/line_receiver.h"),
  "utf8",
);

for (const relativePath of [
  "Core/Src/main.c",
  "Core/Inc/main.h",
  "platform/stm32f103_hal.c",
  "app/src/mcv1_protocol.c",
  "app/include/mcv1_protocol.h",
  "README.md",
]) {
  assert.ok(existsSync(resolve(firmwareDirectory, relativePath)), `${relativePath} must be versioned`);
}

assert.ok(!existsSync(resolve(firmwareDirectory, "f401re-stage1.ioc")));
assert.match(mainSource, /#include "mcv1_protocol\.h"/);
assert.match(mainSource, /#include "stm32f103_hal\.h"/);
assert.match(mainSource, /mcv1_process_line\(/);
assert.match(mainSource, /mcv1_step\(/);
assert.match(mainHeader, /PUMP_CTRL_Pin GPIO_Pin_0/);
assert.match(receiverHeader, /FW_LINE_CAPACITY 128u/);

for (const ignoredPattern of [
  "firmware/**/*.elf",
  "firmware/**/*.axf",
  "firmware/**/*.hex",
  "firmware/**/*.bin",
]) {
  assert.ok(gitignore.includes(ignoredPattern), `${ignoredPattern} must be ignored`);
}

console.log("repository firmware layout: passed");
