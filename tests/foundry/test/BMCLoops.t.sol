// SPDX-License-Identifier: UNLICENSED
pragma solidity =0.8.13;

import "forge-std/Test.sol";

contract BMCLoopsTest is Test {

    function test_countdown_concrete() public returns (uint) {
        uint n = 3;
        while (n > 0) {
            n = n - 1;
        }
        assert(n == 0);
    }

    function test_countdown_symbolic(uint n) public returns (uint) {
        vm.assume(n <= 3);
        while (n > 0) {
            n = n - 1;
        }
        assert(n == 0);
    }
}
