from datetime import date, datetime
from typing import Any


def jsonable_mapping(params: dict[str, Any]) -> dict[str, Any]:
    jsonable: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, datetime | date):
            jsonable[key] = value.isoformat()
        else:
            jsonable[key] = value
    return jsonable
