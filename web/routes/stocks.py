"""Stocks route — return available film stocks and print stocks."""

from fastapi import APIRouter
from core.stocks import (
    get_stocks_by_category, CATEGORIES, get_stock_defaults, _STOCK_DEFS,
)
from core.conversion import PRINT_STOCKS

router = APIRouter()


@router.get("/stocks")
async def list_stocks():
    by_category = get_stocks_by_category()
    return {
        "categories": {k: v for k, v in CATEGORIES.items()},
        "stocks": by_category,
    }


@router.get("/stock-defaults/{key}")
async def stock_defaults(key: str):
    """Return the per-stock baseline values for every UI-exposed pipeline
    parameter. Used to reset sliders when the user selects a different stock."""
    if key not in _STOCK_DEFS:
        return {"error": "Unknown stock"}
    return get_stock_defaults(key)


@router.get("/print-stocks")
async def list_print_stocks():
    return {
        key: info["name"]
        for key, info in PRINT_STOCKS.items()
    }
