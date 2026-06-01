# tests/conftest.py
import sys
from pathlib import Path
import pytest
import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

@pytest.fixture
def in_memory_db():
    """DuckDB in-memory database untuk testing — tidak menyentuh data production."""
    import duckdb
    conn = duckdb.connect(":memory:")
    return conn

@pytest.fixture
def sample_candles_df():
    """100 candle OHLCV palsu untuk testing sinyal."""
    import numpy as np
    n = 220
    np.random.seed(42)
    price = 100.0
    prices = []
    for _ in range(n):
        price *= (1 + np.random.normal(0, 0.02))
        prices.append(price)

    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":   [p * 0.999 for p in prices],
        "high":   [p * 1.005 for p in prices],
        "low":    [p * 0.995 for p in prices],
        "close":  prices,
        "volume": [abs(np.random.normal(1_000_000, 200_000)) for _ in range(n)],
    })
