from typing import Dict

from app.strategies.base import AttackStrategy

_strategies: Dict[str, AttackStrategy] = {}


def register_strategy(strategy: AttackStrategy):
    _strategies[strategy.name] = strategy


def get_strategy(name: str) -> AttackStrategy:
    if name not in _strategies:
        raise ValueError(f"Unknown strategy: '{name}'. Available: {list(_strategies.keys())}")
    return _strategies[name]


def list_strategies() -> list[str]:
    return list(_strategies.keys())


def get_all_strategies() -> Dict[str, AttackStrategy]:
    return dict(_strategies)


# Import and register all built-in strategies
from app.strategies.prompt_injection import PromptInjectionStrategy  # noqa: E402
from app.strategies.roleplay import RoleplayStrategy  # noqa: E402
from app.strategies.instruction_hierarchy import InstructionHierarchyStrategy  # noqa: E402
from app.strategies.encoding import EncodingStrategy  # noqa: E402
from app.strategies.context_manipulation import ContextManipulationStrategy  # noqa: E402
from app.strategies.multi_turn import MultiTurnStrategy  # noqa: E402
from app.strategies.adaptive_mutation import AdaptiveMutationStrategy  # noqa: E402

register_strategy(PromptInjectionStrategy())
register_strategy(RoleplayStrategy())
register_strategy(InstructionHierarchyStrategy())
register_strategy(EncodingStrategy())
register_strategy(ContextManipulationStrategy())
register_strategy(MultiTurnStrategy())
register_strategy(AdaptiveMutationStrategy())
