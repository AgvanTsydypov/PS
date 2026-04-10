/**
 * One-off setup script for creating a Metaplex Core master collection (Devnet).
 *
 * Usage:
 *   node scripts/setup_master_collection.mjs --uri "https://.../collection.json"
 *   node scripts/setup_master_collection.mjs --uri "https://.../collection.json" --royalty-bps 500
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { mplCore, createCollection, ruleSet } from "@metaplex-foundation/mpl-core";
import { createUmi } from "@metaplex-foundation/umi-bundle-defaults";
import {
  createSignerFromKeypair,
  generateSigner,
  keypairIdentity,
  publicKey,
} from "@metaplex-foundation/umi";

const DEVNET_RPC_URL = "https://api.devnet.solana.com";
const DEFAULT_COLLECTION_NAME = "PolyStars Official";
const MASTER_COLLECTION_ENV_KEY = "MASTER_COLLECTION_ADDRESS";

function parseArgs(argv) {
  const args = {
    keypairPath: path.resolve(process.cwd(), "my-keypair.json"),
    uri: process.env.MASTER_COLLECTION_METADATA_URI || "",
    envPath: path.resolve(process.cwd(), ".env"),
    nameOverride: null,
    royaltyBps: null,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    const next = argv[i + 1];
    if (token === "--keypair-path" && next) {
      args.keypairPath = path.resolve(process.cwd(), next);
      i += 1;
    } else if (token === "--uri" && next) {
      args.uri = next;
      i += 1;
    } else if (token === "--env-path" && next) {
      args.envPath = path.resolve(process.cwd(), next);
      i += 1;
    } else if (token === "--name" && next) {
      args.nameOverride = next;
      i += 1;
    } else if (token === "--royalty-bps" && next) {
      args.royaltyBps = Number(next);
      i += 1;
    } else if (token === "--help" || token === "-h") {
      printHelpAndExit(0);
    }
  }

  return args;
}

function printHelpAndExit(code) {
  console.log(`Usage:
  node scripts/setup_master_collection.mjs --uri "https://.../collection.json" [options]

Options:
  --keypair-path <path>   Path to my-keypair.json (default: ./my-keypair.json)
  --env-path <path>       Path to .env (default: ./.env)
  --name <string>         Override on-chain collection name (default: from metadata.name)
  --royalty-bps <number>  Override royalties bps (0..10000). 500 = 5%
  -h, --help              Show help

Notes:
  - --uri should be a REAL public metadata JSON URL (HTTPS, e.g. Pinata gateway).
  - Script validates metadata has name, symbol and image.
  - If --royalty-bps is omitted, uses metadata.seller_fee_basis_points.
`);
  process.exit(code);
}

function loadSecretKey(pathToKeypair) {
  if (!fs.existsSync(pathToKeypair)) {
    throw new Error(`Keypair file not found: ${pathToKeypair}`);
  }
  const raw = fs.readFileSync(pathToKeypair, "utf8");
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error("Keypair JSON must be an array of integers.");
  }
  const secret = Uint8Array.from(parsed);
  if (secret.length !== 64 && secret.length !== 32) {
    throw new Error("Unsupported key length. Expected 32 or 64 bytes.");
  }
  return secret;
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
  const required = ["name", "symbol", "image"];
  for (const field of required) {
    const value = json[field];
    if (typeof value !== "string" || value.trim().length === 0) {
      throw new Error(`Metadata JSON missing required field: ${field}`);
    }
  }
  return json;
}

function resolveRoyaltiesBps(argsRoyaltyBps, metadata) {
  const metadataBps = Number(metadata.seller_fee_basis_points ?? 0);
  const resolved = argsRoyaltyBps ?? metadataBps;
  if (!Number.isFinite(resolved) || resolved < 0 || resolved > 10000) {
    throw new Error("Royalties bps must be a number in range 0..10000.");
  }
  return Math.trunc(resolved);
}

function resolveCreators(metadata, fallbackAddress) {
  const rawCreators = metadata?.properties?.creators;
  if (!Array.isArray(rawCreators) || rawCreators.length === 0) {
    return [{ address: fallbackAddress, percentage: 100 }];
  }

  const creators = rawCreators.map((entry) => ({
    address: publicKey(String(entry.address)),
    percentage: Number(entry.share),
  }));

  const valid = creators.every(
    (creator) =>
      Number.isFinite(creator.percentage) &&
      creator.percentage >= 0 &&
      creator.percentage <= 100
  );
  const total = creators.reduce((acc, c) => acc + c.percentage, 0);
  if (!valid || total !== 100) {
    console.warn(
      "Metadata creators are invalid (share must be integers summing to 100). Falling back to payer=100%."
    );
    return [{ address: fallbackAddress, percentage: 100 }];
  }
  return creators.map((creator) => ({
    address: creator.address,
    percentage: Math.trunc(creator.percentage),
  }));
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.uri || args.uri.includes("placeholder")) {
    throw new Error(
      "Provide a real metadata JSON URI via --uri (placeholder URI is not allowed)."
    );
  }
  console.log("Fetching and validating metadata JSON...");
  const metadata = await fetchMetadataJson(args.uri);
  const onChainName = args.nameOverride?.trim() || metadata.name.trim() || DEFAULT_COLLECTION_NAME;

  console.log("Connecting to Devnet RPC...");
  const umi = createUmi(DEVNET_RPC_URL).use(mplCore());

  console.log(`Loading keypair from ${args.keypairPath}`);
  const secret = loadSecretKey(args.keypairPath);
  const umiKeypair = umi.eddsa.createKeypairFromSecretKey(secret);
  const payerSigner = createSignerFromKeypair(umi, umiKeypair);
  umi.use(keypairIdentity(payerSigner));

  const collectionSigner = generateSigner(umi);
  const resolvedRoyaltyBps = resolveRoyaltiesBps(args.royaltyBps, metadata);
  const resolvedCreators = resolveCreators(metadata, payerSigner.publicKey);
  const plugins = [];
  if (resolvedRoyaltyBps > 0) {
    plugins.push({
      type: "Royalties",
      basisPoints: resolvedRoyaltyBps,
      creators: resolvedCreators,
      ruleSet: ruleSet("None"),
    });
  }

  console.log("Creating collection via Metaplex Core...");
  const tx = await createCollection(umi, {
    collection: collectionSigner,
    name: onChainName,
    uri: args.uri,
    ...(plugins.length ? { plugins } : {}),
  }).sendAndConfirm(umi, { confirm: { commitment: "confirmed" } });

  const collectionAddress = collectionSigner.publicKey.toString();
  const signature = tx.signature.toString();
  const explorerCollectionUrl = `https://explorer.solana.com/address/${collectionAddress}?cluster=devnet`;
  const explorerTxUrl = `https://explorer.solana.com/tx/${signature}?cluster=devnet`;

  upsertEnvValue(args.envPath, MASTER_COLLECTION_ENV_KEY, collectionAddress);

  console.log(`Collection Address: ${collectionAddress}`);
  console.log(`Collection Explorer URL: ${explorerCollectionUrl}`);
  console.log(`Transaction Explorer URL: ${explorerTxUrl}`);
  console.log(`On-chain name: ${onChainName}`);
  console.log(`Metadata symbol: ${metadata.symbol}`);
  console.log(`Metadata image: ${metadata.image}`);
  console.log(`Saved to .env: ${MASTER_COLLECTION_ENV_KEY}=${collectionAddress}`);
  if (resolvedRoyaltyBps > 0) {
    console.log(
      `Royalties configured: ${resolvedRoyaltyBps} bps (${resolvedRoyaltyBps / 100}%)`
    );
  } else {
    console.log("Royalties configured: disabled (0 bps).");
  }
}

main().catch((error) => {
  console.error("Failed to create collection:", error?.message || error);
  process.exit(1);
});
