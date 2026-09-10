"""Shared conversation-lineage helpers for genuine multi-turn red teaming.

The persisted conversation *is* the lineage: attacks chained through
``Attack.parent_attack_id`` form a single escalating exchange, and each attack's
latest result holds the target's response to that turn. These helpers centralise
the single source of truth so the execution path (the messages actually sent to
the target) and the attacker path (the transcript rendered into mutation
prompts) read the same conversation.

* ``load_conversation_lineage`` — walk a chain oldest-first, hydrating results.
* ``latest_response_text`` — the latest target response for one turn.
* ``build_target_messages`` — an OpenAI-style ``[{role, content}, ...]`` list
  interleaving the target's responses and ending with the current turn's prompt.
"""
import uuid

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models.domain import Attack


async def load_conversation_lineage(attack_id: uuid.UUID) -> list[Attack]:
    """Walk ``parent_attack_id`` from ``attack_id`` back to the chain root,
    returning the attacks oldest-first (root first, ``attack_id`` last)."""
    async with AsyncSessionLocal() as session:
        current_id: uuid.UUID | None = attack_id
        chain: list[Attack] = []
        while current_id is not None:
            attack = (
                await session.execute(
                    select(Attack)
                    .where(Attack.id == current_id)
                    .options(selectinload(Attack.results))
                )
            ).scalar_one_or_none()
            if attack is None:
                break
            chain.append(attack)
            current_id = attack.parent_attack_id
    chain.reverse()
    return chain


def latest_response_text(attack: Attack) -> str:
    """The latest target response for a turn, or ``""`` when the attack never
    executed (or its response was empty)."""
    if not attack.results:
        return ""
    latest = max(attack.results, key=lambda r: (r.created_at, r.id))
    return latest.target_response or ""


def build_target_messages(lineage: list[Attack]) -> list[dict]:
    """Build the OpenAI-style message list for executing the *current* turn.

    Every prior turn contributes its user message and the target's latest
    response to it (skipped when missing/empty, so providers never receive a
    stray assistant turn). The current attack contributes only its user message:
    this execution produces that turn's response.
    """
    messages: list[dict] = []
    for index, turn in enumerate(lineage):
        messages.append({"role": "user", "content": turn.prompt_text})
        if index == len(lineage) - 1:
            break
        response = latest_response_text(turn)
        if response:
            messages.append({"role": "assistant", "content": response})
    return messages
