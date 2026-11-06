// node download.mjs <rootHash> <outPath>   (Merkle-proof verified)
import { Downloader, Indexer, StorageNode } from "@0gfoundation/0g-storage-ts-sdk";
import { INDEXER_RPC } from "./common.mjs";

const [root, out] = process.argv.slice(2);
const indexer = new Indexer(INDEXER_RPC);
let err = await indexer.download(root, out, true);
if (err !== null) {
  // the indexer's location index can lag minutes behind a finished upload; ask its trusted nodes directly
  const { trusted } = await indexer.getShardedNodes();
  const holders = [];
  for (const n of trusted) {
    const node = new StorageNode(n.url);
    const info = await node.getFileInfo(root, true).catch(() => null);
    if (info?.finalized) holders.push(node);
  }
  err = holders.length ? await new Downloader(holders).download(root, out, true)
                       : new Error(`no storage node holds ${root} yet`);
}
if (err !== null) {
  console.error(String(err?.message || err));
  process.exit(1);
}
console.log(JSON.stringify({ rootHash: root, saved: out, verified: true }));
