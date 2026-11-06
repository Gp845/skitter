// node register.mjs <rootHash> <episodeId> <decisions>  -> logs the episode in EpisodeRegistry on 0G Chain
import { ethers } from "ethers";
import { signer } from "./common.mjs";

const [root, episodeId, decisions] = process.argv.slice(2);
const address = process.env.REGISTRY_ADDRESS;
if (!address) { console.error("REGISTRY_ADDRESS not set"); process.exit(1); }
const reg = new ethers.Contract(address,
  ["function log(bytes32 root, string episodeId, uint32 decisions)"], signer);
const tx = await reg.log(root, episodeId, Number(decisions));
const rcpt = await tx.wait();
console.log(JSON.stringify({ registry: address, registryTx: rcpt.hash, block: rcpt.blockNumber }));
