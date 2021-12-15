from typing import List, Optional

from wizwalker.memory.memory_object import PropertyClass
from .spell_effect import AddressedSpellEffect


class CombatResolver(PropertyClass):
    async def read_base_address(self) -> int:
        raise NotImplementedError()

    async def bool_global_effect(self) -> bool:
        return await self.read_value_from_offset(112, "bool")

    async def write_bool_global_effect(self, bool_global_effect: bool):
        await self.write_value_to_offset(112, bool_global_effect, "bool")

    async def global_effect(self) -> Optional[AddressedSpellEffect]:
        addr = await self.read_value_from_offset(120, "long long")

        if addr == 0:
            return None

        return AddressedSpellEffect(self.memory_reader, addr)

    async def battlefield_effects(self) -> List[AddressedSpellEffect]:
        effects = []
        for addr in await self.read_shared_vector(136):
            effects.append(AddressedSpellEffect(self.memory_reader, addr))

        return effects


class AddressedCombatResolver(CombatResolver):
    pass
