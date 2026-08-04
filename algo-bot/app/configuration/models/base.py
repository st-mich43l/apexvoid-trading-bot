"""Shared frozen/strict base for Catalog V2 model shells."""

from pydantic import BaseModel, ConfigDict


class FrozenConfigModel(BaseModel):
  model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
