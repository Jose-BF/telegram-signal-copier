"""Pure, private boundary for isolated runtime-versus-replay validation."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RuntimeBrokerIdentity:
    symbol: str
    magic: int
    ticket_base: int
    entry_owner: str = "model"
    history_cost_profile: str | None = None
    valuation_profile: str | None = None

    def __post_init__(self):
        if self.entry_owner not in {"model", "external_runtime"}:
            raise ValueError("explicit entry owner required")
        if self.history_cost_profile not in (None, "synthetic_zero_commission_fee_v1"):
            raise ValueError("unsupported runtime history cost profile")
        if self.history_cost_profile is not None and self.entry_owner != "external_runtime":
            raise ValueError("runtime history requires external entry ownership")
        if self.valuation_profile not in (None, "linear_contract_fx_unrounded_v1"):
            raise ValueError("unsupported runtime valuation hypothesis")
        if not isinstance(self.symbol, str) or not self.symbol or len(self.symbol) > 128:
            raise ValueError("explicit runtime control symbol required")
        for name in ("magic", "ticket_base"):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value < 2**31:
                raise ValueError("positive bounded runtime identity required")


@dataclass(frozen=True)
class NativeProtectionEffect:
    ticket: int
    sl: float
    tp: float
    quote_index: int
    time_ns: int

    def __post_init__(self):
        for name in ("ticket", "quote_index", "time_ns"):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == "ticket" else 0):
                raise ValueError("invalid native protection identity/clock")
        for value in (self.sl, self.tp):
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("native level must be finite and nonnegative")


@dataclass(frozen=True)
class RuntimeBrokerAccess:
    """Model callbacks only. No runtime imports or live-order capability."""

    snapshot: object
    apply_protection: object
    apply_entry: object = None
    observe_entry_response: object = None
    drain_risk: object = None
    history: object = None
    apply_close: object = None
    observe_close_response: object = None
    # Optional unrounded model hypothesis; never native valuation certification.
    calc_profit: object = None
    # Synthetic time-scoped broker history, available only with an explicit cost profile.
    history_range: object = None
    observe_entry_reconciliation: object = None


@dataclass(frozen=True)
class NativeEntryCommand:
    request_id: str
    symbol: str
    magic: int
    direction: str
    volume: float
    requested_price: float
    sl: float
    tp: float
    quote_index: int
    time_ns: int
    comment: str = ""

    def __post_init__(self):
        if any(not isinstance(value, str) or not value or len(value) > 128
               for value in (self.request_id, self.symbol)):
            raise ValueError("explicit native entry identity required")
        if type(self.magic) is not int or self.magic < 1 or self.direction not in {"BUY", "SELL"}:
            raise ValueError("invalid native entry ownership")
        for value in (self.volume, self.requested_price):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("native entry size/price must be finite and positive")
        NativeProtectionEffect(1, self.sl, self.tp, self.quote_index, self.time_ns)
        if not isinstance(self.comment, str) or len(self.comment) > 31:
            raise ValueError("native entry comment must fit the broker request")


@dataclass(frozen=True)
class NativeExecutionReceipt:
    execution_id: int
    request_id: str
    retcode: int
    comment: str
    ticket: int = 0
    price: float = 0.
    volume: float = 0.
    deal_id: int = 0

    def native_result(self):
        return {"retcode": self.retcode, "comment": self.comment, "order": self.ticket,
                "deal": self.deal_id or self.ticket, "price": self.price, "volume": self.volume}


@dataclass(frozen=True)
class NativeEntryResponse:
    execution_id: int
    quote_index: int
    time_ns: int

    def __post_init__(self):
        NativeProtectionEffect(self.execution_id, 0., 0., self.quote_index, self.time_ns)


@dataclass(frozen=True)
class NativeEntryReconciliation:
    request_id: str
    order: int
    deal: int
    symbol: str
    magic: int
    direction: str
    comment: str
    volume: float
    price: float
    quote_index: int
    time_ns: int

    def __post_init__(self):
        for name in ("order", "deal"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError("positive broker identity required")
        NativeEntryCommand(self.request_id, self.symbol, self.magic, self.direction,
                           self.volume, self.price, 0., 0., self.quote_index,
                           self.time_ns, self.comment)


@dataclass(frozen=True)
class NativeCloseCommand:
    request_id: str
    position: int
    symbol: str
    magic: int
    direction: str
    volume: float
    requested_price: float
    quote_index: int
    time_ns: int
    comment: str = ""

    def __post_init__(self):
        NativeProtectionEffect(self.position, 0., 0., self.quote_index, self.time_ns)
        NativeEntryCommand(self.request_id, self.symbol, self.magic, self.direction,
                           self.volume, self.requested_price, 0., 0., self.quote_index,
                           self.time_ns, self.comment)


@dataclass(frozen=True)
class NativeCloseResponse:
    execution_id: int
    quote_index: int
    time_ns: int

    def __post_init__(self):
        NativeProtectionEffect(self.execution_id, 0., 0., self.quote_index, self.time_ns)
