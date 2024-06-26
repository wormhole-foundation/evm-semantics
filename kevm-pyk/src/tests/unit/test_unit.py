import json

from pyk.kast.inner import KSort

from kevm_pyk.hello import hello
from kevm_pyk.solc_to_k import Contract, method_sig_from_abi


def test_hello() -> None:
    assert hello('World') == 'Hello, World!'


test1_abi = """
{
  "inputs": [
    {
      "components": [
        {
          "internalType": "address",
          "name": "target",
          "type": "address"
        },
        {
          "internalType": "bytes",
          "name": "callData",
          "type": "bytes"
        }
      ],
      "internalType": "struct IMulticall3.Call[]",
      "name": "calls",
      "type": "tuple[]"
    }
  ],
  "name": "test1",
  "outputs": [
    {
      "internalType": "uint256",
      "name": "blockNumber",
      "type": "uint256"
    },
    {
      "internalType": "bytes[]",
      "name": "returnData",
      "type": "bytes[]"
    }
  ],
  "stateMutability": "payable",
  "type": "function"
}
"""

test2_abi = """
{
  "inputs": [],
  "name": "test2",
  "outputs": [
    {
      "internalType": "uint256",
      "name": "blockNumber",
      "type": "uint256"
    },
    {
      "internalType": "bytes[]",
      "name": "returnData",
      "type": "bytes[]"
    }
  ],
  "stateMutability": "payable",
  "type": "function"
}
"""


test3_abi = """
{
  "inputs": [
    {
      "internalType": "uint32",
      "name": "blockNumber1",
      "type": "uint32"
    },
    {
      "internalType": "uint256",
      "name": "blockNumber2",
      "type": "uint256"
    }
  ],
  "name": "test3",
  "outputs": [
    {
      "internalType": "bytes32",
      "name": "blockHash",
      "type": "bytes32"
    }
  ],
  "stateMutability": "view",
  "type": "function"
}
"""

test4_abi = """
{
  "inputs": [
    {
      "components": [
        {
          "internalType": "address",
          "name": "target",
          "type": "address"
        },
        {
          "components": [
            {
              "internalType": "address",
              "name": "target",
              "type": "address"
            },
            {
              "internalType": "bytes",
              "name": "callData",
              "type": "bytes"
            }
          ],
          "internalType": "struct IMulticall3.Call[]",
          "name": "calls",
          "type": "tuple[3]"
        }
      ],
      "internalType": "struct IMulticall3.Call[]",
      "name": "calls",
      "type": "tuple[5]"
    }
  ],
  "name": "test4",
  "outputs": [
    {
      "internalType": "uint256",
      "name": "blockNumber",
      "type": "uint256"
    },
    {
      "internalType": "bytes[]",
      "name": "returnData",
      "type": "bytes[]"
    }
  ],
  "stateMutability": "payable",
  "type": "function"
}
"""


def test_method_sig_from_abi() -> None:
    assert method_sig_from_abi(json.loads(test1_abi)) == 'test1((address,bytes)[])'
    assert method_sig_from_abi(json.loads(test2_abi)) == 'test2()'
    assert method_sig_from_abi(json.loads(test3_abi)) == 'test3(uint32,uint256)'
    assert method_sig_from_abi(json.loads(test4_abi)) == 'test4((address,(address,bytes)[3])[5])'


contract_json = """
{
  "abi": [
    {
      "inputs": [
        {
          "components": [
            {
              "internalType": "address",
              "name": "target",
              "type": "address"
            },
            {
              "internalType": "bytes",
              "name": "callData",
              "type": "bytes"
            }
          ],
          "internalType": "struct IMulticall3.Call[]",
          "name": "calls",
          "type": "tuple[]"
        }
      ],
      "name": "aggregate",
      "outputs": [
        {
          "internalType": "uint256",
          "name": "blockNumber",
          "type": "uint256"
        },
        {
          "internalType": "bytes[]",
          "name": "returnData",
          "type": "bytes[]"
        }
      ],
      "stateMutability": "payable",
      "type": "function"
    }
  ],
  "deployedBytecode": {
    "object": "0x",
    "sourceMap": "",
    "linkReferences": {}
  },
  "methodIdentifiers": {
    "aggregate((address,bytes)[])": "252dba42"
  },
  "ast": {
    "absolutePath": "lib/forge-std/src/interfaces/IMulticall3.sol"
  },
  "id": 17
}
"""


def test_contract_creation() -> None:
    contract = Contract('TestContract', json.loads(contract_json), foundry=True)
    assert len(contract.methods) == 1
    method = contract.methods[0]
    assert method.sort == KSort('TestContractMethod')
    assert method.id == int('252dba42', 16)
    assert method.arg_types == ('tuple[]',)
    assert method.contract_name == 'TestContract'
    assert method.payable
