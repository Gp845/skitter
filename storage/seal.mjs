// node seal.mjs encrypt|decrypt <in> <out>   AES-256-GCM with EPISODE_KEY (64 hex chars, from .env)
// Camera frames from a home must not sit readable on public storage. 0G Storage still proves the sealed bytes;
// only the owner's key turns them back into the episode.  Format: "SKT1" | 12-byte IV | ciphertext | 16-byte tag
import fs from "node:fs";
import crypto from "node:crypto";
import "./common.mjs";  // loads .env

const [mode, inPath, outPath] = process.argv.slice(2);
const key = Buffer.from(process.env.EPISODE_KEY || "", "hex");
if (key.length !== 32) { console.error("EPISODE_KEY must be 64 hex chars"); process.exit(1); }
const MAGIC = Buffer.from("SKT1");
const data = fs.readFileSync(inPath);

if (mode === "encrypt") {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv("aes-256-gcm", key, iv);
  const body = Buffer.concat([c.update(data), c.final()]);
  fs.writeFileSync(outPath, Buffer.concat([MAGIC, iv, body, c.getAuthTag()]));
} else if (mode === "decrypt") {
  if (!data.subarray(0, 4).equals(MAGIC)) { console.error("not a sealed episode"); process.exit(1); }
  const d = crypto.createDecipheriv("aes-256-gcm", key, data.subarray(4, 16));
  d.setAuthTag(data.subarray(data.length - 16));
  fs.writeFileSync(outPath, Buffer.concat([d.update(data.subarray(16, data.length - 16)), d.final()]));  // throws if tampered
} else { console.error("usage: node seal.mjs encrypt|decrypt <in> <out>"); process.exit(1); }
console.log(JSON.stringify({ mode, out: outPath }));
