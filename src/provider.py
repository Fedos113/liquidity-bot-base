import logging
import random
import time as _time
from functools import wraps
from typing import Any, Callable

from requests.exceptions import HTTPError, RequestException, Timeout as RequestsTimeout
from web3 import Web3
from web3.exceptions import Web3Exception

from src.config import config
from src.constants import POOL_ABI, ERC20_ABI, POSITION_MANAGER_ABI, WETH_ABI, SWAP_ROUTER_ABI, GAUGE_ABI

logger = logging.getLogger("liqbot")

RATE_LIMIT_CODE = -32005
RATE_LIMIT_WAIT = 60
_MIN_RPC_INTERVAL = 1.0
_last_rpc = 0.0
_rate_limit_until = 0.0


def _throttle():
    global _last_rpc, _rate_limit_until
    now = _time.monotonic()

    if now < _rate_limit_until:
        remaining = _rate_limit_until - now
        logger.warning(f"Rate-limit cooldown active, sleeping {remaining:.0f}s")
        _time.sleep(remaining)
        now = _time.monotonic()

    elapsed = now - _last_rpc
    if elapsed < _MIN_RPC_INTERVAL:
        _time.sleep(_MIN_RPC_INTERVAL - elapsed)
    _last_rpc = _time.monotonic()


def _set_rate_limit_cooldown() -> None:
    global _rate_limit_until
    _rate_limit_until = _time.monotonic() + RATE_LIMIT_WAIT


def _is_rate_limit(err_msg: str) -> bool:
    if "'code': -32005" in err_msg or "rate limit" in err_msg.lower():
        return True
    if "429" in err_msg and "Too Many Requests" in err_msg:
        return True
    return False


def with_retry(max_retries: int = 5, base_delay: int = 1):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (Web3Exception, ConnectionError, TimeoutError, HTTPError, RequestException, RequestsTimeout) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        err_msg = str(e)
                        is_rate_limit = _is_rate_limit(err_msg)
                        if is_rate_limit:
                            delay = RATE_LIMIT_WAIT + random.uniform(0, 5)
                            _set_rate_limit_cooldown()
                        else:
                            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(f"RPC error: {e}. Retry {attempt + 1}/{max_retries} in {delay:.0f}s")
                        _time.sleep(delay)
                    else:
                        logger.error(f"RPC error after {max_retries} retries: {e}")
            raise last_exc
        return wrapper
    return decorator


def get_web3() -> Web3:
    provider = Web3.HTTPProvider(config.RPC_URL, request_kwargs={"timeout": 30})
    orig_make_request = provider.make_request

    def _throttled_make_request(method, params):
        _throttle()
        return orig_make_request(method, params)

    provider.make_request = _throttled_make_request

    w3 = Web3(provider)
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {config.RPC_URL}")
    logger.info(f"Connected to Base. Chain ID: {w3.eth.chain_id}")
    return w3


def get_pool_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.POOL_ADDRESS), abi=POOL_ABI)


def get_position_manager_contract(w3: Web3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(config.POSITION_MANAGER_ADDRESS), abi=POSITION_MANAGER_ABI
    )


def get_erc20_contract(w3: Web3, address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)


def get_weth_contract(w3: Web3):
    return get_erc20_contract(w3, config.WETH_ADDRESS)


def get_usdc_contract(w3: Web3):
    return get_erc20_contract(w3, config.USDC_ADDRESS)


def get_wnative_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.WETH_ADDRESS), abi=WETH_ABI)


def get_swap_router_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.SWAP_ROUTER_ADDRESS), abi=SWAP_ROUTER_ABI)


def get_gauge_contract(w3: Web3):
    return w3.eth.contract(address=Web3.to_checksum_address(config.GAUGE_ADDRESS), abi=GAUGE_ABI)


def get_account(w3: Web3):
    return w3.eth.account.from_key(config.PRIVATE_KEY)


def estimate_gas(w3: Web3, tx: dict) -> int:
    try:
        estimated = w3.eth.estimate_gas(tx)
        return int(estimated * 1.2)
    except Exception:
        return 500_000
