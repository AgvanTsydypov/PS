# PolyStars Collection Setup (Devnet)

This guide describes the current, supported flow for creating the master Metaplex Core collection.

## What is used

- Collection creation script: `scripts/setup_master_collection.mjs`
- Metadata upload script: `scripts/upload_collection_metadata.py`
- Metadata template: `data/metadata/master_collection.devnet.json`
- Wallet keypair file: `my-keypair.json`

## 1) Prepare metadata JSON

Edit `data/metadata/master_collection.devnet.json`:

- `name`
- `symbol`
- `description`
- `image` (public URL to image)
- `seller_fee_basis_points` (e.g. `500` = 5%)
- `properties.creators` (shares must sum to `100`)

Example:

```json
{
  "name": "PolyStars Official",
  "symbol": "POLY",
  "seller_fee_basis_points": 500
}
```

## 2) Add Pinata token to `.env`

Add:

```env
PINATA_JWT=your_pinata_jwt_here
```

## 3) Upload metadata JSON to IPFS

Run:

```bash
python scripts/upload_collection_metadata.py \
  --metadata-file data/metadata/master_collection.devnet.json \
  --update-env
```

This prints:

- `IPFS URI: ipfs://...`
- `HTTPS URI: https://gateway.pinata.cloud/ipfs/...`

And writes to `.env`:

- `MASTER_COLLECTION_METADATA_URI=...`

## 4) Install Node dependencies (first time only)

```bash
npm install
```

## 5) Create collection (Metaplex Core)

Use URI directly (recommended):

```bash
npm run setup:master-collection -- --uri "https://gateway.pinata.cloud/ipfs/QmSyNS7Uz9tnHr7BYMZcfCTyjbZDHfKTvAXbi7diDDfMWF"
```

Optional override:

```bash
npm run setup:master-collection -- --uri "https://gateway.pinata.cloud/ipfs/<HASH>" --royalty-bps 500
```

If `--royalty-bps` is omitted, the script uses `seller_fee_basis_points` from metadata JSON.

## 6) Result

Script outputs:

- `Collection Address`
- `Collection Explorer URL`
- `Transaction Explorer URL`

And updates `.env`:

- `MASTER_COLLECTION_ADDRESS=<new_collection_address>`

## Recreate Base (Zora) collection

Use this when you want a brand new Zora 1155 contract (new collection address).

1) Prepare contract metadata URI (IPFS JSON).

2) Run collection setup:

```bash
npm run setup:zora-collection -- --uri "https://gateway.pinata.cloud/ipfs/<COLLECTION_METADATA_HASH>"
```

Optional:

```bash
npm run setup:zora-collection -- --uri "https://gateway.pinata.cloud/ipfs/<COLLECTION_METADATA_HASH>" --name "PolyStars Base"
```

Important behavior:

- Setup creates only the Zora 1155 collection contract.
- Setup does **not** create bootstrap `tokenId=1`.
- First NFT appears only when mint is triggered from app flow (`/api/claims/mint`).

3) Script writes new values to `.env`:

- `ZORA_1155_CONTRACT_ADDRESS=<new_contract_address>`
- `ZORA_CHAIN=...`
- `ZORA_RPC_URL=...`

4) Copy the same Zora values to `.env.prod` (if minting on VPS), then restart backend:

```bash
docker compose --env-file .env.prod up -d --build web_backend
```

Important:

- Old and new collections are different contracts. Old minted NFTs stay in the old collection.
- Royalty/payout settings are applied for newly created tokens in the new contract flow.

## Notes

- Creator splits are read from `properties.creators`.
- Script validates metadata contains `name`, `symbol`, `image`.
- If creator shares are invalid, script falls back to payer as `100%`.
- Use Devnet explorer URL format:
  - `https://explorer.solana.com/address/<ADDRESS>?cluster=devnet`
