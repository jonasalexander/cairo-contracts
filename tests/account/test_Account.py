import pytest
from starkware.starknet.testing.starknet import Starknet
from starkware.starkware_utils.error_handling import StarkException
from starkware.starknet.definitions.error_codes import StarknetErrorCode
from utils import TestSigner, assert_revert, get_contract_def, cached_contract, TRUE


signer = TestSigner(123456789987654321)
other = TestSigner(987654321123456789)

IACCOUNT_ID = 0xf10dbd44


@pytest.fixture(scope='module')
def contract_defs():
    account_def = get_contract_def('openzeppelin/account/Account.cairo')
    init_def = get_contract_def("tests/mocks/Initializable.cairo")
    attacker_def = get_contract_def("tests/mocks/account_reentrancy.cairo")

    return account_def, init_def, attacker_def


@pytest.fixture(scope='module')
async def account_init(contract_defs):
    account_def, init_def, attacker_def = contract_defs
    starknet = await Starknet.empty()

    account1 = await starknet.deploy(
        contract_def=account_def,
        constructor_calldata=[signer.public_key]
    )
    account2 = await starknet.deploy(
        contract_def=account_def,
        constructor_calldata=[signer.public_key]
    )
    initializable1 = await starknet.deploy(
        contract_def=init_def,
        constructor_calldata=[],
    )
    initializable2 = await starknet.deploy(
        contract_def=init_def,
        constructor_calldata=[],
    )
    attacker = await starknet.deploy(
        contract_def=attacker_def,
        constructor_calldata=[],
    )

    return starknet.state, account1, account2, initializable1, initializable2, attacker


@pytest.fixture
def account_factory(contract_defs, account_init):
    account_def, init_def, attacker_def = contract_defs
    state, account1, account2, initializable1, initializable2, attacker = account_init
    _state = state.copy()
    account1 = cached_contract(_state, account_def, account1)
    account2 = cached_contract(_state, account_def, account2)
    initializable1 = cached_contract(_state, init_def, initializable1)
    initializable2 = cached_contract(_state, init_def, initializable2)
    attacker = cached_contract(_state, attacker_def, attacker)

    return account1, account2, initializable1, initializable2, attacker


@pytest.mark.asyncio
async def test_constructor(account_factory):
    account, *_ = account_factory

    execution_info = await account.get_public_key().call()
    assert execution_info.result == (signer.public_key,)

    execution_info = await account.supportsInterface(IACCOUNT_ID).call()
    assert execution_info.result == (TRUE,)


@pytest.mark.asyncio
async def test_execute(account_factory):
    account, _, initializable, *_ = account_factory

    execution_info = await initializable.initialized().call()
    assert execution_info.result == (0,)

    await signer.send_transactions(account, [(initializable.contract_address, 'initialize', [])])

    execution_info = await initializable.initialized().call()
    assert execution_info.result == (1,)


@pytest.mark.asyncio
async def test_multicall(account_factory):
    account, _, initializable_1, initializable_2, _ = account_factory

    execution_info = await initializable_1.initialized().call()
    assert execution_info.result == (0,)
    execution_info = await initializable_2.initialized().call()
    assert execution_info.result == (0,)

    await signer.send_transactions(
        account,
        [
            (initializable_1.contract_address, 'initialize', []),
            (initializable_2.contract_address, 'initialize', [])
        ]
    )

    execution_info = await initializable_1.initialized().call()
    assert execution_info.result == (1,)
    execution_info = await initializable_2.initialized().call()
    assert execution_info.result == (1,)


@pytest.mark.asyncio
async def test_return_value(account_factory):
    account, _, initializable, *_ = account_factory

    # initialize, set `initialized = 1`
    await signer.send_transactions(account, [(initializable.contract_address, 'initialize', [])])

    read_info = await signer.send_transactions(account, [(initializable.contract_address, 'initialized', [])])
    call_info = await initializable.initialized().call()
    (call_result, ) = call_info.result
    assert read_info.result.response == [call_result]  # 1


@ pytest.mark.asyncio
async def test_nonce(account_factory):
    account, _, initializable, *_ = account_factory

    execution_info = await account.get_nonce().call()
    current_nonce = execution_info.result.res

    # lower nonce
    try:
        await signer.send_transactions(account, [(initializable.contract_address, 'initialize', [])], current_nonce - 1)
        assert False
    except StarkException as err:
        _, error = err.args
        assert error['code'] == StarknetErrorCode.TRANSACTION_FAILED

    # higher nonce
    try:
        await signer.send_transactions(account, [(initializable.contract_address, 'initialize', [])], current_nonce + 1)
        assert False
    except StarkException as err:
        _, error = err.args
        assert error['code'] == StarknetErrorCode.TRANSACTION_FAILED
    # right nonce
    await signer.send_transactions(account, [(initializable.contract_address, 'initialize', [])], current_nonce)

    execution_info = await initializable.initialized().call()
    assert execution_info.result == (1,)


@pytest.mark.asyncio
async def test_public_key_setter(account_factory):
    account, *_ = account_factory

    execution_info = await account.get_public_key().call()
    assert execution_info.result == (signer.public_key,)

    # set new pubkey
    await signer.send_transactions(account, [(account.contract_address, 'set_public_key', [other.public_key])])

    execution_info = await account.get_public_key().call()
    assert execution_info.result == (other.public_key,)


@pytest.mark.asyncio
async def test_public_key_setter_different_account(account_factory):
    account, bad_account, *_ = account_factory

    # set new pubkey
    await assert_revert(
        signer.send_transactions(
            bad_account,
            [(account.contract_address, 'set_public_key', [other.public_key])]
        ),
        reverted_with="Account: caller is not this account"
    )


@pytest.mark.asyncio
async def test_account_takeover_with_reentrant_call(account_factory):
    account, _, _, _, attacker = account_factory

    await assert_revert(
        signer.send_transaction(account, attacker.contract_address, 'account_takeover', []),
        reverted_with="Account: no reentrant call"
    )
    
    execution_info = await account.get_public_key().call()
    assert execution_info.result == (signer.public_key,)
