"""Canonical configuration projection owned by the Python service."""
from __future__ import annotations
from copy import deepcopy
from typing import cast
from pydantic import BaseModel, create_model
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.root import ApexVoidConfig
_PYTHON_RUNTIME_OWNERS = frozenset({'python', 'shared'})

def _leaf_owner(field: object) -> str | None:
    metadata = getattr(field, 'json_schema_extra', None) or {}
    apexvoid = metadata.get('apexvoid_config')
    return None if apexvoid is None else str(apexvoid['owner'])

def _descendant_owners(model: type[BaseModel]) -> frozenset[str]:
    owners: set[str] = set()
    for field in model.model_fields.values():
        owner = _leaf_owner(field)
        if owner is not None:
            owners.add(owner)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            owners.update(_descendant_owners(annotation))
    return frozenset(owners)

def _project_model(model: type[BaseModel], *, projected_name: str | None=None) -> type[BaseModel]:
    """Derive a strict model using canonical FieldInfo and nested model types."""
    owners = _descendant_owners(model)
    if owners <= _PYTHON_RUNTIME_OWNERS:
        return model
    decorators = model.__pydantic_decorators__
    if decorators.field_validators or decorators.model_validators:
        raise TypeError(f'mixed-owner model {model.__name__} has validators that require an explicit projection review')
    definitions: dict[str, tuple[object, object]] = {}
    for field_name, source_field in model.model_fields.items():
        annotation = source_field.annotation
        projected_field = deepcopy(source_field)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            child_owners = _descendant_owners(annotation)
            if not child_owners & _PYTHON_RUNTIME_OWNERS:
                continue
            projected_annotation = _project_model(annotation)
            projected_field.annotation = projected_annotation
            if projected_field.default_factory is annotation:
                projected_field.default_factory = projected_annotation
            definitions[field_name] = (projected_annotation, projected_field)
            continue
        if _leaf_owner(source_field) in _PYTHON_RUNTIME_OWNERS:
            definitions[field_name] = (annotation, projected_field)
    return create_model(projected_name or f'{model.__name__}PythonRuntime', __base__=FrozenConfigModel, __module__=__name__, **definitions)
PythonRuntimeConfig = cast(type[ApexVoidConfig], _project_model(ApexVoidConfig, projected_name='PythonRuntimeConfig'))
PythonRuntimeConfig.__doc__ = 'Frozen canonical root containing Python-owned and shared fields only.'
