// Robots as ENSv2 agent namespaces on Sepolia: <label>.skitter.eth lives in skitter.eth's own UserRegistry and
// points at a per-robot PermissionedResolver where the account holds every role and the robot wallet may only set
// its skitter.* text records (Enhanced Access Control, argument-scoped). Signs with STORAGE_PRIVATE_KEY.
//   node ens.mjs setup                         one-time: registers skitter.eth with its own subname registry
//   node ens.mjs claim <label> <owner> <robot> hub: mints <label>.skitter.eth to <owner>, robot gets key-scoped roles
//   node ens.mjs record <name> <json>          robot: sets its own text records, e.g. {"skitter.episode":"0x…"}
//   node ens.mjs read <name>                   live resolution through the Universal Resolver + robot's roles
import { ethers } from "ethers";
import "./common.mjs"; // loads ../.env

const rpc = new ethers.JsonRpcProvider(process.env.ENS_RPC || "https://ethereum-sepolia-rpc.publicnode.com");
const wallet = process.env.STORAGE_PRIVATE_KEY ? new ethers.Wallet(process.env.STORAGE_PRIVATE_KEY, rpc) : null;
const PARENT = process.env.ENS_PARENT || "skitter.eth";
const A = { // ENSv2 on Sepolia: https://docs.ens.domains/learn/deployments
  ethRegistrar: "0xf633e7fc17e2bbe0d0965d18ec1821dcb754a3d3",
  ethRegistry: "0xd4ebcbbdf463c9c45784603db0ddd499bc44a8b4",
  factory: "0xda70306c98e97ece36f997a21368e53298572991",
  userRegistryImpl: "0x9bd8a88719068d09ecee662f36c0e3856708366a",
  resolverImpl: "0x115eb53f0c60696633855f90b138178fb40b2b2c",
  universalResolver: "0xeEeEEEeE14D718C2B47D9923Deab1335E144EeEe",
  usdc: "0x240b0316df57887dbbe58b586508b19e633a14aa", // MockUSDC, open mint
};
export const ROBOT_KEYS = ["skitter.episode", "skitter.status"];
const ALL_ROLES = (1n << 256n) / 15n; // 0x1111…1: every role and its admin
const ROLE_SET_TEXT = 1n << 4n;
// what the account gets on its name (same bitmap as the .eth registrar): resolver, subnames, transfer
const NAME_ROLES = (1n << 20n) | (1n << 148n) | (1n << 24n) | (1n << 152n) | (1n << 156n);
const FUND = ethers.parseEther(process.env.ENS_FUND_ETH || "0.005"), LOW = FUND / 2n; // robot pays its own record writes

const REGISTRY = ["function register(string,address,address,address,uint256,uint64) returns (uint256)",
  "function getSubregistry(string) view returns (address)", "function getResolver(string) view returns (address)",
  "function setParent(address,string)"];
const RESOLVER = new ethers.Interface(["function initialize((address,uint256)[],bytes[])",
  "function grantSetterRoles(bytes,address) returns (bool)", "function revokeRootRoles(uint256,address) returns (bool)",
  "function hasRootRoles(uint256,address) view returns (bool)", "function setText(bytes,string,string)",
  "function setAddress(bytes,uint256,bytes)", "function multicall(bytes[]) returns (bytes[])",
  "function hasRoles(uint256,uint256,address) view returns (bool)",
  "function text(bytes32,string) view returns (string)", "function addr(bytes32) view returns (address)"]);
const coder = ethers.AbiCoder.defaultAbiCoder();
const salt = (kind, x) => BigInt(ethers.keccak256(coder.encode(["bytes32", x.length === 42 ? "address" : "bytes32",
  "uint256"], [ethers.id(kind), x, 0n])));
const send = async (p) => (await (await p).wait()).hash;

async function deployProxy(impl, s, data) {
  const f = new ethers.Contract(A.factory, ["function deployProxy(address,uint256,bytes) returns (address)",
    "event ProxyDeployed(address indexed sender, address indexed proxyAddress, uint256 salt, address implementation)"], wallet);
  const rc = await (await f.deployProxy(impl, s, data)).wait();
  return [rc.logs.map((l) => { try { return f.interface.parseLog(l); } catch { return null; } })
    .find((e) => e?.name === "ProxyDeployed").args.proxyAddress, rc.hash];
}

async function subregistry() {
  const sub = await new ethers.Contract(A.ethRegistry, REGISTRY, rpc).getSubregistry(PARENT.split(".")[0]);
  if (sub === ethers.ZeroAddress) throw new Error(`${PARENT} has no subname registry: run node ens.mjs setup`);
  return new ethers.Contract(sub, REGISTRY, wallet || rpc);
}

async function setup() {
  const label = PARENT.split(".")[0], out = { parent: PARENT };
  const r = new ethers.Contract(A.ethRegistrar, [
    "function isAvailable(string) view returns (bool)",
    "function getRegisterPrice(string,uint64,address) view returns (uint256,uint256)",
    "function makeCommitment(string,address,bytes32,address,address,uint64,bytes32) pure returns (bytes32)",
    "function commit(bytes32)",
    "function register(string,address,bytes32,address,address,uint64,address,bytes32) returns (uint256)"], wallet);
  if (!(await r.isAvailable(label))) return { ...out, registry: (await subregistry()).target, note: "already set up" };
  const init = new ethers.Interface(["function initialize((address,uint256)[])"])
    .encodeFunctionData("initialize", [[[wallet.address, ALL_ROLES]]]);
  [out.registry, out.registryTx] = await deployProxy(A.userRegistryImpl, salt("UserRegistry", ethers.namehash(PARENT)), init);
  const dur = 365n * 86400n, secret = ethers.hexlify(ethers.randomBytes(32));
  const [base, premium] = await r.getRegisterPrice(label, dur, A.usdc);
  const usdc = new ethers.Contract(A.usdc, ["function mint(address,uint256)", "function approve(address,uint256)"], wallet);
  out.mintTx = await send(usdc.mint(wallet.address, base + premium));
  out.approveTx = await send(usdc.approve(A.ethRegistrar, base + premium));
  out.commitTx = await send(r.commit(await r.makeCommitment(label, wallet.address, secret, out.registry,
    ethers.ZeroAddress, dur, ethers.ZeroHash)));
  await new Promise((ok) => setTimeout(ok, 75_000)); // MIN_COMMITMENT_AGE is 60 s
  out.registerTx = await send(r.register(label, wallet.address, secret, out.registry, ethers.ZeroAddress, dur,
    A.usdc, ethers.ZeroHash));
  out.setParentTx = await send(new ethers.Contract(out.registry, REGISTRY, wallet).setParent(A.ethRegistry, label));
  return out;
}

async function claim(label, owner, robot) {
  if (!/^[a-z0-9-]{3,32}$/.test(label)) throw new Error("label: 3-32 of a-z 0-9 -");
  const name = `${label}.${PARENT}`, dns = ethers.dnsEncode(name), reg = await subregistry();
  if ((await reg.getResolver(label)) !== ethers.ZeroAddress) throw new Error(`${name} is taken`);
  const calls = [ // run inside initialize, where record role checks are skipped
    RESOLVER.encodeFunctionData("setAddress", [dns, 60n, robot]),
    RESOLVER.encodeFunctionData("setText", [dns, "skitter.status", "claimed"])];
  const out = { name, owner, robot };
  // the hub gets ROLE_SET_TEXT_ADMIN only long enough to scope the robot to its keys, then drops it
  [out.resolver, out.resolverTx] = await deployProxy(A.resolverImpl, salt("OwnedResolver", ethers.namehash(name)),
    RESOLVER.encodeFunctionData("initialize", [[[owner, ALL_ROLES], [wallet.address, ROLE_SET_TEXT << 128n]], calls]));
  out.rolesTx = await send(new ethers.Contract(out.resolver, RESOLVER, wallet).multicall([
    ...ROBOT_KEYS.map((k) => RESOLVER.encodeFunctionData("grantSetterRoles",
      [RESOLVER.encodeFunctionData("setText", ["0x", k, ""]), robot])),
    RESOLVER.encodeFunctionData("revokeRootRoles", [ROLE_SET_TEXT << 128n, wallet.address])]));
  out.hubKeptRoles = await new ethers.Contract(out.resolver, RESOLVER, rpc).hasRootRoles(ROLE_SET_TEXT << 128n, wallet.address);
  out.registerTx = await send(reg.register(label, owner, ethers.ZeroAddress, out.resolver, NAME_ROLES,
    BigInt(Math.floor(Date.now() / 1000) + 300 * 86400))); // inside skitter.eth's 1-year registration
  if ((await rpc.getBalance(robot)) < LOW) out.fundTx = await send(wallet.sendTransaction({ to: robot, value: FUND }));
  return out;
}

async function record(name, json) {
  const label = name.slice(0, -PARENT.length - 1), kv = JSON.parse(json);
  const res = new ethers.Contract(await (await subregistry()).getResolver(label), RESOLVER, wallet);
  const dns = ethers.dnsEncode(name);
  return { name, resolver: res.target, ensTx: await send(res.multicall(Object.entries(kv)
    .map(([k, v]) => RESOLVER.encodeFunctionData("setText", [dns, k, String(v)])))) };
}

async function read(name) {
  const ur = new ethers.Contract(A.universalResolver, ["function resolve(bytes,bytes) view returns (bytes,address)"], rpc);
  const node = ethers.namehash(name), dns = ethers.dnsEncode(name);
  const get = async (fn, args) => {
    try {
      const [data, resolver] = await ur.resolve(dns, RESOLVER.encodeFunctionData(fn, [node, ...args]));
      return [RESOLVER.decodeFunctionResult(fn, data)[0], resolver];
    } catch { return [null, null]; }
  };
  const [robot, resolver] = await get("addr", []);
  if (!resolver) return { name, registered: false };
  const texts = Object.fromEntries(await Promise.all(ROBOT_KEYS.map(async (k) => [k, (await get("text", [k]))[0]])));
  const res = new ethers.Contract(resolver, RESOLVER, rpc);
  const can = Object.fromEntries(await Promise.all([...ROBOT_KEYS, "avatar", "url"].map(async (k) =>
    [k, await res.hasRoles(BigInt(ethers.id(k)), ROLE_SET_TEXT, robot)])));
  return { name, registered: true, resolver, robot, texts, robot_can_set: can };
}

const [cmd, ...args] = process.argv.slice(2);
const fn = { setup, claim, record, read }[cmd];
if (!fn) { console.error("usage: node ens.mjs setup|claim|record|read …"); process.exit(1); }
if (cmd !== "read" && !wallet) { console.error("STORAGE_PRIVATE_KEY not set"); process.exit(1); }
console.log(JSON.stringify(await fn(...args), (_, v) => (typeof v === "bigint" ? v.toString() : v)));
