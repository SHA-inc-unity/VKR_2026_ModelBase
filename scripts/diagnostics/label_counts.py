import os
import numpy as np
import pandas as pd
from catboost_floader.core.config import ARTIFACTS_DIR, DIRECTION_DEADBAND, BACKTEST_DIR

# prefer already-merged backtest results which contain 'target_return'
fn_candidates = [
    os.path.join(BACKTEST_DIR, "direct_backtest_results.csv"),
    os.path.join(ARTIFACTS_DIR, "BTCUSDT_1_market_dataset.csv"),
]

fn = None
for f in fn_candidates:
    if os.path.exists(f):
        fn = f
        break

if fn is None:
    print("No suitable input file found among:", fn_candidates)
    raise SystemExit(2)

print("Loading", fn)
df = pd.read_csv(fn)
if "target_return" not in df.columns:
    print("File does not contain 'target_return' column; cannot compute labels:", fn)
    raise SystemExit(3)
arr = pd.to_numeric(df["target_return"], errors="coerce").fillna(0.0).to_numpy(dtype=float)

dead = float(DIRECTION_DEADBAND)
labels = np.sign(arr)
labels[np.abs(arr) < dead] = 0.0
unique, counts = np.unique(labels, return_counts=True)
print("DIRECTION_DEADBAND=", dead)
print(dict(zip(map(int, unique), counts)))
# also print proportions
total = counts.sum()
print({int(u): float(c)/total for u, c in zip(unique, counts)})
