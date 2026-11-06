// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {EpisodeRegistry} from "../src/EpisodeRegistry.sol";

contract EpisodeRegistryTest {
    EpisodeRegistry reg = new EpisodeRegistry();
    bytes32 constant ROOT = keccak256("episode");

    function test_logStoresEpisode() public {
        reg.log(ROOT, "20261106-111754", 7);
        (address robot, uint64 time, uint32 decisions) = reg.episodes(ROOT);
        require(robot == address(this) && time == block.timestamp && decisions == 7, "stored wrong");
        require(reg.count() == 1, "count");
    }

    function test_duplicateRootRejected() public {
        reg.log(ROOT, "a", 1);
        try reg.log(ROOT, "b", 2) { revert("duplicate accepted"); } catch {}
    }

    function test_emptyRootRejected() public {
        try reg.log(bytes32(0), "a", 1) { revert("empty root accepted"); } catch {}
    }
}
