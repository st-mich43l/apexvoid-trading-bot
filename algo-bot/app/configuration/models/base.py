"""Shared frozen/strict base for inactive Phase 2A model shells."""

from pydantic import BaseModel, ConfigDict


class FrozenConfigModel(BaseModel):
  model_config = ConfigDict(frozen=True, extra="forbid")
