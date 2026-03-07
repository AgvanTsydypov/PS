#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import { createPublicClient, createWalletClient, http } from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { base, baseSepolia } from "viem/chains";
import { createNew1155Token } from "@zoralabs/protocol-sdk";
import { zoraCreator1155ImplABI } from "@zoralabs/protocol-deployments";

const PINATA_JSON_API_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS";
const PINATA_FILE_API_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS";

function parseArgs(argv) {
  const args = {
    payloadFile: "",
  };
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    const next = argv[i + 1];
    if (token === "--payload-file" && next) {
      args.payloadFile = next;
      i += 1;
    }
  }
  if (!args.payloadFile) {
    throw new Error("Missing required argument --payload-file");
  }
  return args;
}

function resolveChain(chainName) {
  const normalized = String(chainName || "").trim().toLowerCase();
  if (normalized === "base" || normalized === "base-mainnet") return base;
  return baseSepolia;
}

function normalizePrivateKey(privateKey) {
  const raw = String(privateKey || "").trim().replace(/^['"]|['"]$/g, "");
  if (!raw) return "";
  return raw.startsWith("0x") ? raw : `0x${raw}`;
}

function normalizeEvmAddress(value) {
  const raw = String(value || "").trim().replace(/^['"]|['"]$/g, "");
  if (!raw) return "";
  const withPrefix = raw.startsWith("0x") ? raw : `0x${raw}`;
  if (!/^0x[a-fA-F0-9]{40}$/.test(withPrefix)) return "";
  return withPrefix;
}

function resolveRoyaltyBps(rawValue) {
  const fallback = 1000;
  const raw = String(rawValue || "").trim().replace(/^['"]|['"]$/g, "");
  if (!raw) return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || parsed > 10000) {
    throw new Error("ZORA_ROYALTY_BPS must be a number in range 0..10000");
  }
  return Math.trunc(parsed);
}

function guessMediaType(url, fallbackType) {
  const lower = String(url || "").toLowerCase();
  if (fallbackType) return fallbackType;
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".gif")) return "image/gif";
  if (lower.endsWith(".svg")) return "image/svg+xml";
  return "application/octet-stream";
}

function guessExtensionFromContentType(contentType) {
  const normalized = String(contentType || "").toLowerCase();
  if (normalized === "image/png") return ".png";
  if (normalized === "image/jpeg") return ".jpg";
  if (normalized === "image/webp") return ".webp";
  if (normalized === "image/gif") return ".gif";
  if (normalized === "image/svg+xml") return ".svg";
  return ".bin";
}

async function uploadJsonToPinata(json, jwt, nameHint) {
  const response = await fetch(PINATA_JSON_API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      pinataContent: json,
      pinataMetadata: {
        name: `${nameHint || "polystars-zora"}.json`,
      },
    }),
  });
  if (!response.ok) {
    throw new Error(`Pinata JSON upload failed: ${response.status} ${response.statusText}`);
  }
  const body = await response.json();
  const ipfsHash = body?.IpfsHash;
  if (!ipfsHash) throw new Error("Pinata JSON upload response missing IpfsHash");
  return `https://gateway.pinata.cloud/ipfs/${ipfsHash}`;
}

async function uploadImageToPinata(sourceUrl, jwt, nameHint) {
  const sourceResp = await fetch(sourceUrl);
  if (!sourceResp.ok) {
    throw new Error(`Failed to fetch source image: ${sourceResp.status} ${sourceResp.statusText}`);
  }
  const arrayBuffer = await sourceResp.arrayBuffer();
  const mime = guessMediaType(
    sourceUrl,
    (sourceResp.headers.get("content-type") || "").split(";")[0].trim() || undefined,
  );
  const ext = guessExtensionFromContentType(mime);
  const fileName = `${(nameHint || "polystars-image").replace(/[^a-zA-Z0-9_-]+/g, "-")}${ext}`;

  const form = new FormData();
  form.append("file", new Blob([arrayBuffer], { type: mime }), fileName);
  form.append("pinataMetadata", JSON.stringify({ name: fileName }));

  const uploadResp = await fetch(PINATA_FILE_API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
    },
    body: form,
  });
  if (!uploadResp.ok) {
    throw new Error(`Pinata file upload failed: ${uploadResp.status} ${uploadResp.statusText}`);
  }
  const uploadBody = await uploadResp.json();
  const ipfsHash = uploadBody?.IpfsHash;
  if (!ipfsHash) throw new Error("Pinata file upload response missing IpfsHash");
  return {
    pinnedUrl: `https://gateway.pinata.cloud/ipfs/${ipfsHash}`,
    mime,
  };
}

function buildExplorerBase(chain) {
  if (chain.id === base.id) return "https://basescan.org";
  return "https://sepolia.basescan.org";
}

async function main() {
  const args = parseArgs(process.argv);
  const payloadRaw = fs.readFileSync(path.resolve(process.cwd(), args.payloadFile), "utf8");
  const payload = JSON.parse(payloadRaw);

  const chain = resolveChain(process.env.ZORA_CHAIN || "base-sepolia");
  const rpcUrl = (process.env.ZORA_RPC_URL || "").trim() || chain.rpcUrls.default.http[0];
  const privateKey = normalizePrivateKey(process.env.ZORA_MINTER_PRIVATE_KEY || "");
  const contractAddress = (process.env.ZORA_1155_CONTRACT_ADDRESS || "").trim();
  const pinataJwt = (process.env.PINATA_JWT || "").trim();
  const royaltyBps = resolveRoyaltyBps(process.env.ZORA_ROYALTY_BPS || "");

  if (!privateKey || privateKey === "0x") throw new Error("ZORA_MINTER_PRIVATE_KEY is required");
  if (!contractAddress) throw new Error("ZORA_1155_CONTRACT_ADDRESS is required");
  if (!pinataJwt) throw new Error("PINATA_JWT is required");

  const account = privateKeyToAccount(privateKey);
  const payoutRecipient =
    normalizeEvmAddress(process.env.ZORA_PAYOUT_RECIPIENT || "") || account.address;
  const publicClient = createPublicClient({
    chain,
    transport: http(rpcUrl),
  });
  const walletClient = createWalletClient({
    account,
    chain,
    transport: http(rpcUrl),
  });
  let nextNonce = await publicClient.getTransactionCount({
    address: account.address,
    blockTag: "pending",
  });

  const writeContractWithNonce = async (params) => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const nonceToUse = nextNonce;
      try {
        const txHash = await walletClient.writeContract({
          ...params,
          nonce: nonceToUse,
        });
        nextNonce = nonceToUse + 1;
        return txHash;
      } catch (error) {
        const message = String(error?.message || error || "").toLowerCase();
        const isNonceError =
          message.includes("nonce too low") ||
          message.includes("replacement transaction underpriced") ||
          message.includes("already known");
        if (!isNonceError || attempt === 2) {
          throw error;
        }
        nextNonce = await publicClient.getTransactionCount({
          address: account.address,
          blockTag: "pending",
        });
      }
    }
    throw new Error("Failed to send transaction with managed nonce");
  };

  const claimId = Number(payload.claim_id || 0);
  const seasonName = String(payload.season_name || "season");
  const pnl = Number(payload.pnl_value || 0);
  const rank = Number(payload.rank || 0);
  const recipient = String(payload.user_wallet_address || "").trim();
  const winnerContext = payload.winner_context || {};
  const snapshot = winnerContext.snapshot || {};
  const sourceImage = String(snapshot.event_image_url || "").trim();
  const nftName = `PolyStars ${seasonName} #${claimId}`;

  let pinnedImageUrl = "";
  let pinnedImageMime = "";
  if (sourceImage) {
    const uploadedImage = await uploadImageToPinata(sourceImage, pinataJwt, nftName);
    pinnedImageUrl = uploadedImage.pinnedUrl;
    pinnedImageMime = uploadedImage.mime;
  }

  const metadata = {
    name: nftName,
    symbol: "POLY",
    description: `PolyStars reward NFT for season ${seasonName}`,
    attributes: [
      { trait_type: "Profit", value: pnl },
      { trait_type: "Rank", value: rank },
    ],
    winner_context: {
      ...winnerContext,
      snapshot: {
        ...snapshot,
        event_image_source_url: sourceImage || null,
        event_image_url: pinnedImageUrl || null,
      },
    },
  };
  if (pinnedImageUrl) {
    metadata.image = pinnedImageUrl;
    metadata.properties = {
      category: "image",
      files: [{ uri: pinnedImageUrl, type: pinnedImageMime || "image/*" }],
    };
  }

  const metadataUri = await uploadJsonToPinata(metadata, pinataJwt, nftName);

  const onchainContractGetter = {
    async getContractInfo({ contractAddress: targetContractAddress }) {
      const [name, contractVersion, nextTokenId, mintFee] = await Promise.all([
        publicClient.readContract({
          address: targetContractAddress,
          abi: zoraCreator1155ImplABI,
          functionName: "name",
        }),
        publicClient.readContract({
          address: targetContractAddress,
          abi: zoraCreator1155ImplABI,
          functionName: "contractVersion",
        }),
        publicClient.readContract({
          address: targetContractAddress,
          abi: zoraCreator1155ImplABI,
          functionName: "nextTokenId",
        }),
        publicClient.readContract({
          address: targetContractAddress,
          abi: zoraCreator1155ImplABI,
          functionName: "mintFee",
        }),
      ]);
      return {
        name: String(name),
        contractVersion: String(contractVersion),
        nextTokenId: BigInt(nextTokenId),
        mintFee: BigInt(mintFee),
      };
    },
  };

  const tokenCreate = await createNew1155Token({
    contractAddress,
    account,
    token: {
      tokenMetadataURI: metadataUri,
      maxSupply: 1n,
      royaltyBPS: royaltyBps,
      payoutRecipient,
      salesConfig: {
        type: "fixedPrice",
        pricePerToken: 0n,
      },
    },
    chainId: chain.id,
    contractGetter: onchainContractGetter,
  });

  const createTxHash = await writeContractWithNonce(tokenCreate.parameters);
  await publicClient.waitForTransactionReceipt({ hash: createTxHash });

  // Use adminMint directly (owner/admin path). This bypasses minter role and
  // sale strategy restrictions that affect the regular mint() flow.
  const mintTxHash = await writeContractWithNonce({
    address: contractAddress,
    abi: zoraCreator1155ImplABI,
    functionName: "adminMint",
    args: [recipient, tokenCreate.tokenId, 1n, "0x"],
  });
  await publicClient.waitForTransactionReceipt({ hash: mintTxHash });

  const tokenId = tokenCreate.tokenId.toString();
  const explorerBase = buildExplorerBase(chain);
  const output = {
    asset_address: `${contractAddress}:${tokenId}`,
    tx_hash: mintTxHash,
    nft_name: nftName,
    metadata_uri: metadataUri,
    explorer_tx_url: `${explorerBase}/tx/${mintTxHash}`,
    explorer_asset_url: `${explorerBase}/token/${contractAddress}?a=${tokenId}`,
  };
  process.stdout.write(JSON.stringify(output));
}

main().catch((error) => {
  process.stderr.write(String(error?.message || error));
  process.exit(1);
});

