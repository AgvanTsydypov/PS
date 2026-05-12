import hre from "hardhat";

// Verifies the deployed POLYSTARS contract on Etherscan using the same
// constructor args that scripts/deploy.js read from .env.collection.
// Usage: npx hardhat run scripts/verify.js --network mainnet
async function main() {
  const address = process.env.EVM_CONTRACT_ADDRESS;
  if (!address) throw new Error("Set EVM_CONTRACT_ADDRESS in .env.collection (printed by deploy.js)");

  const [deployer] = await hre.ethers.getSigners();
  const ADMIN    = process.env.ADMIN_ADDRESS    || deployer.address;
  const TREASURY = process.env.TREASURY_ADDRESS || deployer.address;
  const MINTER   = process.env.MINTER_ADDRESS   || deployer.address;
  const ROYALTY_BPS  = Number(process.env.ROYALTY_BPS || 100);
  const CONTRACT_URI = process.env.CONTRACT_METADATA_URI || "ipfs://REPLACE_ME";

  console.log("Verifying", address, "with args:");
  console.log({ ADMIN, TREASURY, MINTER, ROYALTY_BPS, CONTRACT_URI });

  await hre.run("verify:verify", {
    address,
    constructorArguments: [ADMIN, TREASURY, MINTER, ROYALTY_BPS, CONTRACT_URI],
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
