import fs from "node:fs";
import path from "node:path";

const dataDir = path.resolve("test-results", "e2e-data");
fs.rmSync(dataDir, { recursive: true, force: true });
fs.mkdirSync(dataDir, { recursive: true });
