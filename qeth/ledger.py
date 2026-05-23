import json
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal


LEDGER_LIVE = "44'/60'/{i}'/0/0"
LEGACY = "44'/60'/0'/{i}"
BIP44 = "44'/60'/0'/0/{i}"

PATH_SCHEMES: dict[str, str] = {
    "Ledger Live": LEDGER_LIVE,
    "Legacy": LEGACY,
    "BIP44 Standard": BIP44,
}

AUTO_STOP_CONSECUTIVE_ZEROS = 3
AUTO_DETECT_HARD_CAP = 100


@dataclass
class DiscoveredAccount:
    address: str
    path: str
    index: int
    balance_wei: int = 0


class LedgerWorker(QThread):
    """Enumerates Ledger accounts in a background thread.

    If `count` is 0, scans until `AUTO_STOP_CONSECUTIVE_ZEROS` empty accounts
    in a row (up to `AUTO_DETECT_HARD_CAP`). Balances are fetched from
    `rpc_url` if provided.
    """

    discovered = Signal(object)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, scheme: str, count: int, rpc_url: str | None = None, parent=None):
        super().__init__(parent)
        self.scheme = scheme
        self.count = count
        self.rpc_url = rpc_url

    def run(self) -> None:
        try:
            from ledgereth.accounts import get_account_by_path
            from ledgereth.comms import init_dongle
        except ImportError as e:
            self.failed.emit(f"ledgereth not installed: {e}")
            return

        try:
            dongle = init_dongle()
        except Exception as e:
            self.failed.emit(
                "Could not open Ledger. Make sure the device is connected, "
                f"unlocked, and the Ethereum app is open.\n\n{e}"
            )
            return

        template = PATH_SCHEMES.get(self.scheme)
        if template is None:
            self.failed.emit(f"Unknown derivation scheme: {self.scheme}")
            return

        auto = self.count == 0
        max_scan = AUTO_DETECT_HARD_CAP if auto else self.count
        consecutive_zero = 0

        try:
            for i in range(max_scan):
                path = template.format(i=i)
                acct = get_account_by_path(path, dongle=dongle)
                balance = self._balance(acct.address) if self.rpc_url else 0
                self.discovered.emit(DiscoveredAccount(
                    address=acct.address, path=path, index=i, balance_wei=balance
                ))
                if auto:
                    if balance == 0:
                        consecutive_zero += 1
                        if consecutive_zero >= AUTO_STOP_CONSECUTIVE_ZEROS:
                            break
                    else:
                        consecutive_zero = 0
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(f"Error reading account: {e}")

    def _balance(self, address: str) -> int:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
            "params": [address, "latest"],
        }
        req = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                # DRPC's Cloudflare front rejects requests with the default
                # Python-urllib/x.y User-Agent (HTTP 403, "error code: 1010").
                "User-Agent": "qeth/0.1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            result = data.get("result")
            if isinstance(result, str):
                return int(result, 16)
        except Exception:
            pass
        return 0
