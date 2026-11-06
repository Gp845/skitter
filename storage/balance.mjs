import { ethers } from "ethers";
import { provider, signer } from "./common.mjs";

const bal = await provider.getBalance(signer.address);
console.log(JSON.stringify({ address: signer.address, balance_0G: ethers.formatEther(bal) }));
