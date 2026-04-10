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

## Base/Zora flow

The legacy Base/Zora mint flow has been removed from this repository. `COLLECTION_SETUP.md` now only covers the Solana master collection setup above.

## Notes

- Creator splits are read from `properties.creators`.
- Script validates metadata contains `name`, `symbol`, `image`.
- If creator shares are invalid, script falls back to payer as `100%`.
- Use Devnet explorer URL format:
  - `https://explorer.solana.com/address/<ADDRESS>?cluster=devnet`
