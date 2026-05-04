from .whole_foods import WholeFoodsAdapter
from .trader_joes import TraderJoesAdapter
from .walmart import WalmartAdapter
from .costco import CostcoAdapter
from .amazon import AmazonAdapter
from .kroger import KrogerAdapter
from .publix import PublixAdapter

ADAPTERS = {
    "whole_foods": WholeFoodsAdapter,
    "trader_joes": TraderJoesAdapter,
    "walmart": WalmartAdapter,
    "costco": CostcoAdapter,
    "amazon": AmazonAdapter,
    "kroger": KrogerAdapter,
    "publix": PublixAdapter,
}


def get_adapter(retailer: str):
    cls = ADAPTERS.get(retailer)
    if cls is None:
        raise KeyError(f"Unknown retailer: {retailer}")
    return cls()
