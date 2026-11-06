// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// On-chain index of Cortex robot episodes. Each episode's full log (voice, 0G request IDs,
/// frames, commands) lives on 0G Storage under `root`; this contract records who logged it and when.
contract EpisodeRegistry {
    struct Episode {
        address robot;
        uint64 time;
        uint32 decisions;
    }

    mapping(bytes32 => Episode) public episodes;
    uint256 public count;

    event EpisodeLogged(bytes32 indexed root, address indexed robot, string episodeId, uint32 decisions);

    function log(bytes32 root, string calldata episodeId, uint32 decisions) external {
        require(root != bytes32(0), "empty root");
        require(episodes[root].time == 0, "already logged");
        episodes[root] = Episode(msg.sender, uint64(block.timestamp), decisions);
        count++;
        emit EpisodeLogged(root, msg.sender, episodeId, decisions);
    }
}
