"""Stocks route — return available film stocks and print stocks."""

from fastapi import APIRouter
from core.stocks import get_stocks_by_category, CATEGORIES
from core.conversion import PRINT_STOCKS

router = APIRouter()


@router.get("/stocks")
async def list_stocks():
    by_category = get_stocks_by_category()
    return {
        "categories": {k: v for k, v in CATEGORIES.items()},
        "stocks": by_category,
    }


@router.get("/print-stocks")
async def list_print_stocks():
    return {
        key: info["name"]
        for key, info in PRINT_STOCKS.items()
    }
