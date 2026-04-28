import hre from "hardhat";
const { ethers } = hre;

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying with:", deployer.address);
  console.log("Balance:", ethers.formatEther(await ethers.provider.getBalance(deployer.address)), "ETH");

  const CONTRACT_URI     = process.env.CONTRACT_METADATA_URI || "https://gateway.pinata.cloud/ipfs/REPLACE_ME";
  const ROYALTY_RECEIVER = process.env.ROYALTY_RECEIVER      || deployer.address;
  const ROYALTY_BPS      = 400; // 4%

  const Factory  = await ethers.getContractFactory("SLOPNFT");
  const contract = await Factory.deploy(
    deployer.address,
    CONTRACT_URI,
    ROYALTY_RECEIVER,
    ROYALTY_BPS,
  );

  await contract.waitForDeployment();
  const address = await contract.getAddress();

  console.log("\n✅ SLOPNFT deployed to:", address);
  console.log("   Owner:           ", deployer.address);
  console.log("   Royalty receiver:", ROYALTY_RECEIVER);
  console.log("   Royalty:         ", ROYALTY_BPS / 100, "%");
  console.log("   contractURI:     ", CONTRACT_URI);
  console.log("\nAdd to .env:");
  console.log(`EVM_CONTRACT_ADDRESS=${address}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
