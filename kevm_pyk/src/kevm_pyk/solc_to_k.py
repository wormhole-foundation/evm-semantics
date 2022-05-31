import functools
import json
import logging
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, Dict, Final, List

from pyk.cli_utils import run_process
from pyk.kast import (
    TRUE,
    KApply,
    KAtt,
    KClaim,
    KDefinition,
    KFlatModule,
    KImport,
    KInner,
    KNonTerminal,
    KProduction,
    KRequire,
    KRewrite,
    KRule,
    KSequence,
    KSort,
    KTerminal,
    KToken,
    KVariable,
)
from pyk.kastManip import buildRule, substitute
from pyk.ktool import KPrint, paren
from pyk.prelude import intToken, stringToken
from pyk.utils import intersperse

from .utils import (
    abstract_cell_vars,
    build_empty_configuration_cell,
    infGas,
    kevmAccountCell,
)

_LOGGER: Final = logging.getLogger(__name__)


def solc_compile(contract_file: Path) -> Dict[str, Any]:
    args = {
        'language': 'Solidity',
        'sources': {
            contract_file.name: {
                'urls': [
                    str(contract_file),
                ],
            },
        },
        'settings': {
            'outputSelection': {
                '*': {
                    '*': [
                        'abi',
                        'storageLayout',
                        'evm.methodIdentifiers',
                        'evm.deployedBytecode.object',
                    ],
                },
            },
        },
    }

    try:
        process_res = run_process(['solc', '--standard-json'], _LOGGER, input=json.dumps(args))
    except CalledProcessError as err:
        raise RuntimeError('solc error', err.stdout, err.stderr)

    return json.loads(process_res.stdout)


def gen_claims_for_contract(kevm: KPrint, contract_name: str) -> List[KClaim]:
    empty_config = build_empty_configuration_cell(kevm.definition, KSort('KevmCell'))
    program = KApply('binRuntime', [KApply('contract_' + contract_name)])
    account_cell = kevmAccountCell(KVariable('ACCT_ID'), KVariable('ACCT_BALANCE'), program, KVariable('ACCT_STORAGE'), KVariable('ACCT_ORIGSTORAGE'), KVariable('ACCT_NONCE'))
    init_subst = {
        'MODE_CELL': KToken('NORMAL', 'Mode'),
        'SCHEDULE_CELL': KApply('LONDON_EVM'),
        'CALLSTACK_CELL': KApply('.List'),
        'CALLDEPTH_CELL': intToken(0),
        'PROGRAM_CELL': program,
        'JUMPDESTS_CELL': KApply('#computeValidJumpDests', [program]),
        'ORIGIN_CELL': KVariable('ORIGIN_ID'),
        'ID_CELL': KVariable('ACCT_ID'),
        'CALLER_CELL': KVariable('CALLER_ID'),
        'LOCALMEM_CELL': KApply('.Memory_EVM-TYPES_Memory'),
        'MEMORYUSED_CELL': intToken(0),
        'WORDSTACK_CELL': KApply('.WordStack_EVM-TYPES_WordStack'),
        'PC_CELL': intToken(0),
        'GAS_CELL': infGas(KVariable('VGAS')),
        'K_CELL': KSequence([KApply('#execute_EVM_KItem'), KVariable('CONTINUATION')]),
        'ACCOUNTS_CELL': KApply('_AccountCellMap_', [account_cell, KVariable('ACCOUNTS')]),
    }
    final_subst = {'K_CELL': KSequence([KApply('#halt_EVM_KItem'), KVariable('CONTINUATION')])}
    init_term = substitute(empty_config, init_subst)
    final_term = abstract_cell_vars(substitute(empty_config, final_subst))
    claim, _ = buildRule(contract_name.lower(), init_term, final_term, claim=True)
    assert isinstance(claim, KClaim)
    return [claim]


def gen_spec_modules(definition_dir: Path, spec_module_name: str) -> str:
    kevm = KPrint(str(definition_dir))
    kevm.symbol_table = kevmSymbolTable(kevm.symbol_table)
    production_labels = [prod.klabel for module in kevm.definition for prod in module.productions if prod.klabel is not None]
    contract_names = [prod_label.name[9:] for prod_label in production_labels if prod_label.name.startswith('contract_')]
    claims = [claim for name in contract_names for claim in gen_claims_for_contract(kevm, name)]
    spec_module = KFlatModule(spec_module_name, claims, [KImport(kevm.definition.main_module_name)])
    spec_defn = KDefinition(spec_module_name, [spec_module], [KRequire('verification.k')])
    return kevm.pretty_print(spec_defn)


def solc_to_k(definition_dir: Path, contract_file: Path, contract_name: str, generate_storage: bool):
    kevm = KPrint(str(definition_dir))
    kevm.symbol_table = kevmSymbolTable(kevm.symbol_table)

    solc_json = solc_compile(contract_file)
    contract_json = solc_json['contracts'][contract_file.name][contract_name]
    storage_layout = contract_json['storageLayout']
    abi = contract_json['abi']
    hashes = contract_json['evm']['methodIdentifiers']
    bin_runtime = '0x' + contract_json['evm']['deployedBytecode']['object']

    # TODO: add check to kevm:
    # solc version should be >=0.8.0 due to:
    # https://github.com/ethereum/solidity/issues/10276

    contract_sort = KSort(f'{contract_name}Contract')

    storage_sentences = generate_storage_sentences(contract_name, contract_sort, storage_layout) if generate_storage else []
    function_sentences = generate_function_sentences(contract_name, contract_sort, abi)
    function_selector_alias_sentences = generate_function_selector_alias_sentences(contract_name, contract_sort, hashes)

    contract_subsort = KProduction(KSort('Contract'), [KNonTerminal(contract_sort)])
    contract_production = KProduction(contract_sort, [KTerminal(contract_name)], att=KAtt({'klabel': f'contract_{contract_name}', 'symbol': ''}))
    contract_macro = KRule(KRewrite(KApply('binRuntime', [KApply(contract_name)]), _parseByteStack(stringToken(bin_runtime))))

    binRuntimeModuleName = contract_name.upper() + '-BIN-RUNTIME'
    binRuntimeModule = KFlatModule(
        binRuntimeModuleName,
        [contract_subsort, contract_production] + storage_sentences + function_sentences + [contract_macro] + function_selector_alias_sentences,
        [KImport('BIN-RUNTIME', True)],
    )
    binRuntimeDefinition = KDefinition(binRuntimeModuleName, [binRuntimeModule], requires=[KRequire('edsl.md')])

    kevm.symbol_table['hashedLocation'] = lambda lang, base, offset: '#hashedLocation(' + lang + ', ' + base + ', ' + offset + ')'  # noqa
    kevm.symbol_table['abiCallData']    = lambda fname, *args: '#abiCallData(' + fname + "".join(", " + arg for arg in args) + ')'  # noqa
    kevm.symbol_table['address']        = _typed_arg_unparser('address')                                                            # noqa
    kevm.symbol_table['bool']           = _typed_arg_unparser('bool')                                                               # noqa
    kevm.symbol_table['bytes']          = _typed_arg_unparser('bytes')                                                              # noqa
    kevm.symbol_table['bytes4']         = _typed_arg_unparser('bytes4')                                                             # noqa
    kevm.symbol_table['bytes32']        = _typed_arg_unparser('bytes32')                                                            # noqa
    kevm.symbol_table['int256']         = _typed_arg_unparser('int256')                                                             # noqa
    kevm.symbol_table['uint256']        = _typed_arg_unparser('uint256')                                                            # noqa
    kevm.symbol_table['rangeAddress']   = lambda t: '#rangeAddress(' + t + ')'                                                      # noqa
    kevm.symbol_table['rangeBool']      = lambda t: '#rangeBool(' + t + ')'                                                         # noqa
    kevm.symbol_table['rangeBytes']     = lambda n, t: '#rangeBytes(' + n + ', ' + t + ')'                                          # noqa
    kevm.symbol_table['rangeUInt']      = lambda n, t: '#rangeUInt(' + n + ', ' + t + ')'                                           # noqa
    kevm.symbol_table['rangeSInt']      = lambda n, t: '#rangeSInt(' + n + ', ' + t + ')'                                           # noqa
    kevm.symbol_table['binRuntime']     = lambda s: '#binRuntime(' + s + ')'                                                        # noqa
    kevm.symbol_table[contract_name]    = lambda: contract_name                                                                     # noqa

    return kevm.pretty_print(binRuntimeDefinition) + '\n'


# KEVM instantiation of pyk

def kevmSymbolTable(symbol_table):
    symbol_table['abi_selector']                                  = lambda a: 'selector(' + a + ')'                                     # noqa
    symbol_table['_orBool_']                                      = paren(symbol_table['_orBool_'])                                     # noqa
    symbol_table['_andBool_']                                     = paren(symbol_table['_andBool_'])                                    # noqa
    symbol_table['notBool_']                                      = paren(symbol_table['notBool_'])                                     # noqa
    symbol_table['_/Int_']                                        = paren(symbol_table['_/Int_'])                                       # noqa
    symbol_table['#Or']                                           = paren(symbol_table['#Or'])                                          # noqa
    symbol_table['#And']                                          = paren(symbol_table['#And'])                                         # noqa
    symbol_table['_Set_']                                         = paren(symbol_table['_Set_'])                                        # noqa
    symbol_table['_|->_']                                         = paren(symbol_table['_|->_'])                                        # noqa
    symbol_table['_Map_']                                         = paren(lambda m1, m2: m1 + '\n' + m2)                                # noqa
    symbol_table['_AccountCellMap_']                              = paren(lambda a1, a2: a1 + '\n' + a2)                                # noqa
    symbol_table['AccountCellMapItem']                            = lambda k, v: v                                                      # noqa
    symbol_table['_[_:=_]_EVM-TYPES_Memory_Memory_Int_ByteArray'] = lambda m, k, v: m + ' [ '  + k + ' := (' + v + '):ByteArray ]'      # noqa
    symbol_table['_[_.._]_EVM-TYPES_ByteArray_ByteArray_Int_Int'] = lambda m, s, w: '(' + m + ' [ ' + s + ' .. ' + w + ' ]):ByteArray'  # noqa
    symbol_table['_<Word__EVM-TYPES_Int_Int_Int']                 = paren(lambda a1, a2: '(' + a1 + ') <Word ('  + a2 + ')')            # noqa
    symbol_table['_>Word__EVM-TYPES_Int_Int_Int']                 = paren(lambda a1, a2: '(' + a1 + ') >Word ('  + a2 + ')')            # noqa
    symbol_table['_<=Word__EVM-TYPES_Int_Int_Int']                = paren(lambda a1, a2: '(' + a1 + ') <=Word (' + a2 + ')')            # noqa
    symbol_table['_>=Word__EVM-TYPES_Int_Int_Int']                = paren(lambda a1, a2: '(' + a1 + ') >=Word (' + a2 + ')')            # noqa
    symbol_table['_==Word__EVM-TYPES_Int_Int_Int']                = paren(lambda a1, a2: '(' + a1 + ') ==Word (' + a2 + ')')            # noqa
    symbol_table['_s<Word__EVM-TYPES_Int_Int_Int']                = paren(lambda a1, a2: '(' + a1 + ') s<Word (' + a2 + ')')            # noqa
    return symbol_table


# Helpers

def generate_storage_sentences(contract_name, contract_sort, storage_layout):
    storage_sort = KSort(f'{contract_name}Storage')
    storage_sentence_pairs = _extract_storage_sentences(contract_name, storage_sort, storage_layout)

    if not storage_sentence_pairs:
        return []

    storage_productions, storage_rules = map(list, zip(*storage_sentence_pairs))
    storage_location_production = KProduction(KSort('Int'), [KNonTerminal(contract_sort), KTerminal('.'), KNonTerminal(storage_sort)], att=KAtt({'klabel': f'storage_{contract_name}', 'macro': ''}))
    return storage_productions + [storage_location_production] + storage_rules


def _extract_storage_sentences(contract_name, storage_sort, storage_layout):
    types = storage_layout.get('types', [])  # 'types' is missing from storage_layout if storage_layout['storage'] == []

    def recur(syntax, lhs, rhs, var_idx, type_name):
        type_dict = types[type_name]
        encoding = type_dict['encoding']

        if encoding == 'inplace':
            members = type_dict.get('members')
            if members:
                return recur_struct(syntax, lhs, rhs, var_idx, members)

            type_label = type_dict['label']
            return recur_value(syntax, lhs, rhs, type_label)

        if encoding == 'mapping':
            key_type_name = type_dict['key']
            value_type_name = type_dict['value']
            return recur_mapping(syntax, lhs, rhs, var_idx, key_type_name, value_type_name)

        if encoding == 'bytes':
            type_label = type_dict['label']
            assert type_label in {'bytes', 'string'}
            return recur_value(syntax, lhs, rhs, type_label)

        raise ValueError(f'Unsupported encoding: {encoding}')

    def recur_value(syntax, lhs, rhs, type_label):
        _check_supported_value_type(type_label)

        # TODO: add structure to LHS:
        # generate (uncurried) unparser, function name, and list of arguments

        # TODO: simplify RHS:
        # #hashedLocation(L, #hashedLocation(L, B, X), Y) => #hashedLocation(L, B, X Y)
        # 0 +Int X => X
        # X +Int 0 => X
        return [(KProduction(storage_sort, syntax),
                 KRule(KRewrite(KToken(lhs, None), rhs)))]

    def recur_struct(syntax, lhs, rhs, var_idx, members, gen_dot=True):
        res = []
        for member in members:
            member_label = member['label']
            member_slot = member['slot']
            member_offset = member['offset']
            member_type_name = member['type']

            if member_offset != 0:
                raise ValueError(f'Unsupported nonzero offset for variable: {member_label}')

            new_syntax = syntax + ([KTerminal('.')] if gen_dot else []) + [KTerminal(member_label)]
            new_lhs = f'{lhs}.{member_label}'
            new_rhs = KApply('_+Int_', [rhs, intToken(member_slot)])
            res += recur(new_syntax, new_lhs, new_rhs, var_idx, member_type_name)
        return res

    def recur_mapping(syntax, lhs, rhs, var_idx, key_type_name, value_type_name):
        key_type_dict = types[key_type_name]
        key_type_label = key_type_dict['label']

        _check_supported_key_type(key_type_label)
        key_sort = _evm_base_sort(key_type_label)

        new_syntax = syntax + [KTerminal('['), KNonTerminal(key_sort), KTerminal(']')]
        new_lhs = f'{lhs}[V{var_idx}]'
        new_rhs = KApply('hashedLocation', [stringToken('Solidity'), rhs, KVariable(f'V{var_idx}')])
        new_type_name = value_type_name
        return recur(new_syntax, new_lhs, new_rhs, var_idx + 1, new_type_name)

    storage = storage_layout['storage']
    return recur_struct([], f'{contract_name}', intToken('0'), 0, storage, gen_dot=False)


def generate_function_sentences(contract_name, contract_sort, abi):
    function_sort = KSort(f'{contract_name}Function')
    function_call_data_production = KProduction(KSort('ByteArray'), [KNonTerminal(contract_sort), KTerminal('.'), KNonTerminal(function_sort)], att=KAtt({'klabel': f'function_{contract_name}', 'symbol': '', 'function': ''}))
    function_sentence_pairs = _extract_function_sentences(contract_name, function_sort, abi)

    if not function_sentence_pairs:
        return []

    function_productions, function_rules = map(list, zip(*function_sentence_pairs))
    return [function_call_data_production] + function_productions + function_rules


def generate_function_selector_alias_sentences(contract_name, contract_sort, hashes):
    abi_function_selector_rules = []
    for h in hashes:
        f_name = h.split('(')[0]
        hash_int = int(hashes[h], 16)
        abi_function_selector_rewrite = KRewrite(KApply('abi_selector', [stringToken(f'{f_name}')]), intToken(hash_int))
        abi_function_selector_rules.append(KRule(abi_function_selector_rewrite))
    return abi_function_selector_rules


def _extract_function_sentences(contract_name, function_sort, abi):
    def extract_production(name, inputs):
        input_types = [input_dict['type'] for input_dict in inputs]

        items = []
        items.append(KTerminal(name))
        items.append(KTerminal('('))

        input_nonterminals = (KNonTerminal(_evm_base_sort(input_type)) for input_type in input_types)
        items += intersperse(input_nonterminals, KTerminal(','))

        items.append(KTerminal(')'))
        return KProduction(function_sort, items)

    def extract_rule(name, inputs):
        input_names = normalize_input_names([input_dict['name'] for input_dict in inputs])
        input_types = [input_dict['type'] for input_dict in inputs]
        lhs = extract_lhs(name, input_names)
        rhs = extract_rhs(name, input_names, input_types)
        ensures = extract_ensures(input_names, input_types)
        return KRule(KRewrite(lhs, rhs), ensures=ensures)

    def extract_lhs(name, input_names):
        # TODO: add structure to LHS:
        # generate (uncurried) unparser, function name, and list of arguments
        return KToken(f'{contract_name}.{name}(' + ', '.join(input_names) + ')', 'ByteArray')

    def extract_rhs(name, input_names, input_types):
        args = [KApply('abi_type_' + input_type, [KVariable(input_name)]) for input_name, input_type in zip(input_names, input_types)] or [KToken('.TypedArgs', 'TypedArgs')]
        return KApply('abiCallData', [stringToken(name)] + args)

    def extract_ensures(input_names, input_types):
        opt_conjuncts = [_range_predicate(KVariable(input_name), input_type) for input_name, input_type in zip(input_names, input_types)]
        conjuncts = [opt_conjunct for opt_conjunct in opt_conjuncts if opt_conjunct is not None]
        if len(conjuncts) == 0:
            return TRUE

        return functools.reduce(lambda x, y: KApply('_andBool_', [x, y]), conjuncts)

    def normalize_input_names(input_names):
        if not input_names:
            return []

        head, *_ = input_names

        if head == '':
            assert all(input_name == '' for input_name in input_names)
            return [f'V{i}' for i, _ in enumerate(input_names)]

        assert all(input_name != '' for input_name in input_names)

        res = [input_dict['name'].lstrip('_').upper() for input_dict in inputs]
        if len(res) != len(set(res)):
            raise RuntimeError(f"Some arguments only differ in capitalization or a '_' prefix for: {name}")
        return res

    res = []
    for func_dict in abi:
        if func_dict['type'] != 'function':
            continue

        name = func_dict['name']
        inputs = func_dict['inputs']
        res.append((extract_production(name, inputs), extract_rule(name, inputs)))
    return res


def _parseByteStack(s: KInner):
    return KApply('#parseByteStack(_)_SERIALIZATION_ByteArray_String', [s])


def _typed_arg_unparser(type_label: str):
    return lambda x: '#' + type_label + '(' + x + ')'


def _check_supported_value_type(type_label: str) -> None:
    supported_value_types = {'address', 'bool', 'bytes32', 'uint8', 'int256', 'string', 'uint256'}
    if type_label not in supported_value_types and not type_label.startswith('contract '):
        raise ValueError(f'Unsupported value type: {type_label}')


def _check_supported_key_type(type_label: str) -> None:
    supported_key_types = {'address', 'bytes32', 'int256', 'uint256'}
    if type_label not in supported_key_types:
        raise ValueError(f'Unsupported key type: {type_label}')


def _evm_base_sort(type_label: str):
    if type_label in {'address', 'bool', 'bytes4', 'bytes32', 'int256', 'uint256', 'uint8'}:
        return KSort('Int')

    if type_label == 'bytes':
        return KSort('ByteArray')

    raise ValueError(f'EVM base sort unknown for: {type_label}')


def _range_predicate(term, type_label: str):
    if type_label == 'address':
        return KApply('rangeAddress', [term])
    if type_label == 'bool':
        return KApply('rangeBool', [term])
    if type_label == 'bytes4':
        return KApply('rangeBytes', [intToken(4), term])
    if type_label in {'bytes32', 'uint256'}:
        return KApply('rangeUInt', [intToken(256), term])
    if type_label == 'int256':
        return KApply('rangeSInt', [intToken(256), term])
    if type_label == 'uint8':
        return KApply('rangeUInt', [intToken(8), term])
    if type_label == 'bytes':
        return None

    raise ValueError(f'Range predicate unknown for: {type_label}')