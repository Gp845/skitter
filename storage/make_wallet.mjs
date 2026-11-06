// Generates a throwaway testnet wallet and stores its key in ../.env (never printed).
import fs from "node:fs";
import { ethers } from "ethers";

const envPath = new URL("../.env", import.meta.url);
let env = fs.readFileSync(envPath, "utf8");
const m = env.match(/^STORAGE_PRIVATE_KEY=(.*)$/m);
let wallet;
if (m && m[1].trim()) {
  wallet = new ethers.Wallet(m[1].trim());
  console.log("existing wallet kept");
} else {
  wallet = ethers.Wallet.createRandom();
  env = env.replace(/^STORAGE_PRIVATE_KEY=.*$/m, `STORAGE_PRIVATE_KEY=${wallet.privateKey}`);
  fs.writeFileSync(envPath, env);
  console.log("new wallet created, key saved to .env");
}
console.log("ADDRESS:", wallet.address);
