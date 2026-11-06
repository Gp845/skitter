// node lookup.mjs <rootHash>  -> the episode's entry in EpisodeRegistry on 0G Chain (read-only, no key, no gas)
import { ethers } from "ethers";
import { provider } from "./common.mjs";

const [root] = process.argv.slice(2);
const address = process.env.REGISTRY_ADDRESS || "0x7d3dA1F5e4358D4b71bCB5fb4465613DEB1161d6";  // Galileo deployment
const reg = new ethers.Contract(address,
  ["function episodes(bytes32) view returns (address robot, uint64 time, uint32 decisions)"], provider);
const e = await reg.episodes(root);
console.log(JSON.stringify({ registry: address, found: e.time > 0n, robot: e.robot,
                             time: Number(e.time), decisions: Number(e.decisions) }));
