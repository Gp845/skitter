// Replace stuck pending txs (nonce latest..pending-1) with 0-value self-transfers at a higher gas price.
import { ethers } from "ethers";
import { provider, signer } from "./common.mjs";

const latest = await provider.getTransactionCount(signer.address, "latest");
const pending = await provider.getTransactionCount(signer.address, "pending");
console.log({ latest, pending });
const gasPrice = ethers.parseUnits(process.argv[2] || "10", "gwei");
for (let n = latest; n < pending; n++) {
  const tx = await signer.sendTransaction({ to: signer.address, value: 0, nonce: n, gasPrice, gasLimit: 21000 });
  console.log("replacement sent nonce", n, tx.hash);
  const r = await Promise.race([tx.wait(), new Promise((res) => setTimeout(() => res(null), 60000))]);
  console.log(r ? `  mined in block ${r.blockNumber}` : "  not mined within 60s");
  if (!r) break;
}
console.log({ latest: await provider.getTransactionCount(signer.address, "latest"),
              pending: await provider.getTransactionCount(signer.address, "pending") });
