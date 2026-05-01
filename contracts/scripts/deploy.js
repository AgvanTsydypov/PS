import hre from "hardhat";
const { ethers } = hre;

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying with:", deployer.address);
  console.log("Balance:", ethers.formatEther(await ethers.provider.getBalance(deployer.address)), "ETH");

  // For Sepolia testing all four roles collapse to the deployer wallet.
  // For mainnet pass distinct ADMIN / TREASURY / MINTER addresses via env.
  const ADMIN    = process.env.ADMIN_ADDRESS    || deployer.address;
  const TREASURY = process.env.TREASURY_ADDRESS || deployer.address;
  const MINTER   = process.env.MINTER_ADDRESS   || deployer.address;

  const ROYALTY_BPS  = Number(process.env.ROYALTY_BPS || 500); // 5%
  const CONTRACT_URI = process.env.CONTRACT_METADATA_URI || "https://gateway.pinata.cloud/ipfs/REPLACE_ME";

  const Factory  = await ethers.getContractFactory("SLOPNFT");
  const contract = await Factory.deploy(
    ADMIN,
    TREASURY,
    MINTER,
    ROYALTY_BPS,
    CONTRACT_URI,
  );

  await contract.waitForDeployment();
  const address = await contract.getAddress();

  console.log("\n✅ SLOPNFT deployed to:", address);
  console.log("   Admin (DEFAULT_ADMIN_ROLE):", ADMIN);
  console.log("   Minter (MINTER_ROLE):      ", MINTER);
  console.log("   Treasury (royalty receiver):", TREASURY);
  console.log("   Royalty:                   ", ROYALTY_BPS / 100, "%");
  console.log("   contractURI:               ", CONTRACT_URI);
  console.log("\nAdd to .env:");
  console.log(`EVM_CONTRACT_ADDRESS=${address}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
