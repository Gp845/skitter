// node upload.mjs <file>  ->  prints one JSON line: {"rootHash","txHash"}  (errors -> stderr, exit 1)
import { ZgFile, Indexer } from "@0gfoundation/0g-storage-ts-sdk";
import { INDEXER_RPC, RPC_URL, signer } from "./common.mjs";

const path = process.argv[2];
const file = await ZgFile.fromFilePath(path);
try {
  const [tree, treeErr] = await file.merkleTree();
  if (treeErr !== null) throw new Error(`merkle tree: ${treeErr}`);
  const indexer = new Indexer(INDEXER_RPC);
  // "resync": file tx already on-chain but some shard never got the data -> push segments again, no new tx
  const opts = process.argv[3] === "resync" ? { skipIfFinalized: false, skipTx: true } : undefined;
  const [tx, err] = await indexer.upload(file, RPC_URL, signer, opts);
  if (err !== null) throw new Error(`upload: ${err}`);
  const out = "rootHash" in tx
    ? { rootHash: tx.rootHash, txHash: tx.txHash }
    : { rootHash: tx.rootHashes[0], txHash: tx.txHashes[0], rootHashes: tx.rootHashes };
  console.log(JSON.stringify({ ...out, localRoot: tree.rootHash() }));
} catch (e) {
  console.error(String(e?.message || e));
  process.exitCode = 1;
} finally {
  await file.close();
}
