// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";
import "@openzeppelin/contracts/token/common/ERC2981.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract SLOPNFT is ERC721URIStorage, ERC2981, Ownable {

    uint256 private _nextTokenId;
    string  private _contractMetadataURI;

    uint256 public constant MAX_BATCH = 100;

    event BatchMinted(uint256 indexed firstTokenId, uint256 count);

    constructor(
        address initialOwner,
        string  memory contractURI_,
        address royaltyReceiver,
        uint96  royaltyBps
    )
        ERC721("PolyStars", "SLOP")
        Ownable(initialOwner)
    {
        _contractMetadataURI = contractURI_;
        _setDefaultRoyalty(royaltyReceiver, royaltyBps);
    }

    function mintTo(
        address to,
        string calldata uri
    ) external onlyOwner returns (uint256 tokenId) {
        tokenId = _nextTokenId++;
        _safeMint(to, tokenId);
        _setTokenURI(tokenId, uri);
    }

    function batchMintTo(
        address[] calldata recipients,
        string[]  calldata uris
    ) external onlyOwner returns (uint256 firstTokenId) {
        uint256 count = recipients.length;
        require(count > 0,            "Empty batch");
        require(count == uris.length, "Length mismatch");
        require(count <= MAX_BATCH,   "Exceeds MAX_BATCH");

        firstTokenId = _nextTokenId;

        for (uint256 i = 0; i < count; ) {
            uint256 tokenId = _nextTokenId++;
            _safeMint(recipients[i], tokenId);
            _setTokenURI(tokenId, uris[i]);
            unchecked { ++i; }
        }

        emit BatchMinted(firstTokenId, count);
    }

    function contractURI() external view returns (string memory) {
        return _contractMetadataURI;
    }

    function setContractURI(string calldata newURI) external onlyOwner {
        _contractMetadataURI = newURI;
    }

    function setDefaultRoyalty(
        address receiver,
        uint96  feeNumerator
    ) external onlyOwner {
        _setDefaultRoyalty(receiver, feeNumerator);
    }

    function setTokenRoyalty(
        uint256 tokenId,
        address receiver,
        uint96  feeNumerator
    ) external onlyOwner {
        _setTokenRoyalty(tokenId, receiver, feeNumerator);
    }

    function totalMinted() external view returns (uint256) {
        return _nextTokenId;
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721URIStorage, ERC2981)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
