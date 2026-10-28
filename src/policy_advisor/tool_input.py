"""A base for models the model fills through a tool call.

Tool use sometimes sends a nested array as a JSON-encoded string
(`"[{\"locator\": ...}]"`) rather than an array. The first nightly run
(2026-10-28) lost a whole extraction job to one such field. A string that
decodes to a list is read as that list; anything else still fails validation,
so a malformed record is refused rather than guessed at.
"""

import json
from typing import Any, get_origin

from pydantic import BaseModel, model_validator


class ToolInputModel(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _decode_json_lists(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        decoded = dict(data)
        for name, field in cls.model_fields.items():
            value = decoded.get(name)
            if get_origin(field.annotation) is list and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    continue
                if isinstance(parsed, list):
                    decoded[name] = parsed
        return decoded
