from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from .exceptions import PolicyError


class Authority(StrEnum):
    AUTO = "auto"
    HUMAN_APPROVAL = "human_approval"
    FORBIDDEN = "forbidden"


class _PolicyModel(BaseModel):
    version: int
    default: Authority
    tools: dict[str, Authority]

    @field_validator("version")
    @classmethod
    def _v1_only(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported policy version {v}, expected 1")
        return v


class Policy:
    def __init__(self, model: _PolicyModel):
        self._model = model

    @property
    def version(self) -> int:
        return self._model.version

    @property
    def default(self) -> Authority:
        return self._model.default

    def authority(self, tool_name: str) -> Authority:
        return self._model.tools.get(tool_name, self._model.default)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        try:
            text = Path(path).read_text()
        except FileNotFoundError as e:
            raise PolicyError(f"policy file not found: {path}") from e
        except OSError as e:
            raise PolicyError(f"cannot read policy file: {path}") from e

        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise PolicyError(f"invalid YAML in {path}: {e}") from e

        try:
            model = _PolicyModel.model_validate(raw)
        except ValidationError as e:
            raise PolicyError(f"invalid policy schema in {path}: {e}") from e

        return cls(model)
