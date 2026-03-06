/**
 * One-off setup script for creating a Zora 1155 collection contract (Base/Base Sepolia).
 *
 * Usage:
 *   node scripts/setup_zora_collection.mjs --uri "https://.../collection.json"
 *   node scripts/setup_zora_collection.mjs --uri "https://.../collection.json" --name "PolyStars Base"
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { createPublicClient, createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base, baseSepolia } from "viem/chains";
import { create1155, getContractAddressFromReceipt } from "@zoralabs/protocol-sdk";

const DEFAULT_ENV_PATH = path.resolve(process.cwd(), ".env");
const ZORA_CHAIN_ENV_KEY = "ZORA_CHAIN";
const ZORA_RPC_ENV_KEY = "ZORA_RPC_URL";
const ZORA_MINTER_KEY_ENV_KEY = "ZORA_MINTER_PRIVATE_KEY";
const ZORA_CONTRACT_ENV_KEY = "ZORA_1155_CONTRACT_ADDRESS";
const ZORA_CONTRACT_METADATA_URI_ENV_KEY = "ZORA_CONTRACT_METADATA_URI";
const DEFAULT_COLLECTION_NAME = "PolyStars Base";

function parseArgs(argv) {
  const args = {
    envPath: DEFAULT_ENV_PATH,
    chain: "",
    rpcUrl: "",
    privateKey: "",
    uri: "",
    tokenUri: "",
    name: "",
  };

  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    const next = argv[i + 1];

    if (token === "--env-path" && next) {
      args.envPath = path.resolve(process.cwd(), next);
      i += 1;
    } else if (token === "--chain" && next) {
      args.chain = next.trim();
      i += 1;
    } else if (token === "--rpc-url" && next) {
      args.rpcUrl = next.trim();
      i += 1;
    } else if (token === "--private-key" && next) {
      args.privateKey = next.trim();
      i += 1;
    } else if (token === "--uri" && next) {
      args.uri = next.trim();
      i += 1;
    } else if (token === "--token-uri" && next) {
      args.tokenUri = next.trim();
      i += 1;
    } else if (token === "--name" && next) {
      args.name = next.trim();
      i += 1;
    } else if (token === "--help" || token === "-h") {
      printHelpAndExit(0);
    }
  }

  return args;
}

function printHelpAndExit(code) {
  console.log(`Usage:
  node scripts/setup_zora_collection.mjs --uri "https://.../collection.json" [options]

Options:
  --env-path <path>      Path to .env (default: ./.env)
  --chain <name>         base-sepolia | base (default: from .env or base-sepolia)
  --rpc-url <url>        RPC URL (default: from .env or chain default)
  --private-key <key>    Minter private key (default: from .env)
  --uri <url>            Contract metadata URI
  --token-uri <url>      First token metadata URI (default: same as --uri)
  --name <string>        Contract name (default: PolyStars Base)
  -h, --help             Show help

Notes:
  - Zora 1155 contract is your collection-level container on Base.
  - Script creates contract + first token setup transaction.
  - It saves ZORA_1155_CONTRACT_ADDRESS to .env.
`);
  process.exit(code);
}

function readEnvFile(envPath) {
  if (!fs.existsSync(envPath)) return new Map();
  const content = fs.readFileSync(envPath, "utf8");
  const lines = content.split(/\r?\n/);
  const envMap = new Map();
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim().replace(/^['"]|['"]$/g, "");
    envMap.set(key, value);
  }
  return envMap;
}

function getEnvValue(envMap, key) {
  const fromProcess = process.env[key];
  if (fromProcess && fromProcess.trim()) return fromProcess.trim();
  const fromFile = envMap.get(key);
  if (fromFile && String(fromFile).trim()) return String(fromFile).trim();
  return "";
}

function upsertEnvValue(envPath, key, value) {
  const exists = fs.existsSync(envPath);
  const original = exists ? fs.readFileSync(envPath, "utf8") : "";
  const lines = original.length > 0 ? original.split(/\r?\n/) : [];
  const nextLine = `${key}=${value}`;
  let replaced = false;
  const updated = lines
    .filter((line, idx, arr) => !(idx === arr.length - 1 && line === ""))
    .map((line) => {
      if (line.startsWith(`${key}=`)) {
        replaced = true;
        return nextLine;
      }
      return line;
    });
  if (!replaced) updated.push(nextLine);
  fs.writeFileSync(envPath, `${updated.join("\n")}\n`, "utf8");
}

function resolveChain(chainName) {
  const normalized = String(chainName || "").trim().toLowerCase();
  if (normalized === "base" || normalized === "base-mainnet") return base;
  return baseSepolia;
}

function normalizePrivateKey(privateKey) {
  const raw = String(privateKey || "").trim();
  if (!raw) return "";
  return raw.startsWith("0x") ? raw : `0x${raw}`;
}

async function fetchMetadataJson(uri) {
  const response = await fetch(uri, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Metadata fetch failed: ${response.status} ${response.statusText}`);
  }

  let json;
  try {
    json = await response.json();
  } catch {
    throw new Error("Metadata URI did not return valid JSON.");
  }
  if (!json || typeof json !== "object" || Array.isArray(json)) {
    throw new Error("Metadata JSON must be an object.");
  }
  const required = ["name", "symbol"];
  for (const field of required) {
    const value = json[field];
    if (typeof value !== "string" || value.trim().length === 0) {
      throw new Error(`Metadata JSON missing required field: ${field}`);
    }
  }
  return json;
}

function getExplorerBase(chain) {
  return chain.id === base.id ? "https://basescan.org" : "https://sepolia.basescan.org";
}

async function main() {
  const args = parseArgs(process.argv);
  const envMap = readEnvFile(args.envPath);

  const chainName = args.chain || getEnvValue(envMap, ZORA_CHAIN_ENV_KEY) || "base-sepolia";
  const chain = resolveChain(chainName);

  const rpcUrl =
    args.rpcUrl ||
    getEnvValue(envMap, ZORA_RPC_ENV_KEY) ||
    chain.rpcUrls.default.http[0];

  const privateKeyRaw = args.privateKey || getEnvValue(envMap, ZORA_MINTER_KEY_ENV_KEY);
  const privateKey = normalizePrivateKey(privateKeyRaw);
  if (!privateKey || privateKey === "0x") {
    throw new Error(
      `Missing private key. Provide --private-key or set ${ZORA_MINTER_KEY_ENV_KEY} in .env.`
    );
  }

  const contractUri =
    args.uri ||
    getEnvValue(envMap, ZORA_CONTRACT_METADATA_URI_ENV_KEY) ||
    "";
  if (!contractUri || contractUri.includes("placeholder")) {
    throw new Error(
      "Provide a real contract metadata URI via --uri or ZORA_CONTRACT_METADATA_URI."
    );
  }
  const tokenUri = args.tokenUri || contractUri;
  const contractName = args.name || DEFAULT_COLLECTION_NAME;

  console.log("Validating contract metadata URI...");
  const metadata = await fetchMetadataJson(contractUri);

  console.log(`Connecting to ${chain.name}...`);
  const account = privateKeyToAccount(privateKey);
  const publicClient = createPublicClient({
    chain,
    transport: http(rpcUrl),
  });
  const walletClient = createWalletClient({
    account,
    chain,
    transport: http(rpcUrl),
  });

  console.log("Preparing Zora 1155 contract create transaction...");
  const prepared = await create1155({
    contract: {
      name: contractName,
      uri: contractUri,
    },
    account,
    token: {
      tokenMetadataURI: tokenUri,
      maxSupply: 1n,
      salesConfig: {
        type: "fixedPrice",
        pricePerToken: 0n,
      },
    },
    publicClient,
  });

  const predictedContractAddress = prepared.contractAddress;

  console.log("Sending transaction...");
  const txHash = await walletClient.writeContract(prepared.parameters);
  const receipt = await publicClient.waitForTransactionReceipt({ hash: txHash });

  let contractAddress = predictedContractAddress;
  try {
    contractAddress = getContractAddressFromReceipt(receipt);
  } catch {
    // Fallback to predicted deterministic address from SDK.
  }

  const explorerBase = getExplorerBase(chain);
  const contractExplorerUrl = `${explorerBase}/address/${contractAddress}`;
  const txExplorerUrl = `${explorerBase}/tx/${txHash}`;

  upsertEnvValue(args.envPath, ZORA_CONTRACT_ENV_KEY, contractAddress);
  upsertEnvValue(args.envPath, ZORA_CHAIN_ENV_KEY, chainName);
  upsertEnvValue(args.envPath, ZORA_RPC_ENV_KEY, rpcUrl);

  console.log(`Contract Address: ${contractAddress}`);
  console.log(`Contract Explorer URL: ${contractExplorerUrl}`);
  console.log(`Transaction Explorer URL: ${txExplorerUrl}`);
  console.log(`Contract name: ${contractName}`);
  console.log(`Contract metadata name: ${metadata.name}`);
  console.log(`Saved to .env: ${ZORA_CONTRACT_ENV_KEY}=${contractAddress}`);
}

main().catch((error) => {
  console.error("Failed to create Zora collection:", error?.message || error);
  process.exit(1);
});

