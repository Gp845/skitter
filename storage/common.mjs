import fs from "node:fs";
import { ethers } from "ethers";

// minimal .env loader (no extra dependency)
const envFile = new URL("../.env", import.meta.url);
if (fs.existsSync(envFile)) {
  for (const line of fs.readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const m = line.match(/^([A-Z_]+)=(.*)$/);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2];
  }
}
export const RPC_URL = process.env.STORAGE_RPC || "https://evmrpc-testnet.0g.ai";
export const INDEXER_RPC = process.env.STORAGE_INDEXER || "https://indexer-storage-testnet-turbo.0g.ai";
export const provider = new ethers.JsonRpcProvider(RPC_URL);
// null without a key: read-only scripts (download, lookup) must work for auditors who have no wallet
export const signer = process.env.STORAGE_PRIVATE_KEY ? new ethers.Wallet(process.env.STORAGE_PRIVATE_KEY, provider) : null;
