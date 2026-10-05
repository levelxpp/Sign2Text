# Friend's hand_signals.csv — LR vs MLP on same 80/20 split

- CSV: `hand_signals.csv` (12,458 rows, 42-dim MediaPipe Hands pixel coords)
- Split: stratified test_size=0.2, random_state=42 → 9,966 train / 2,492 test (same as friend's letter_server.py)

## Headline (test accuracy)
| Model | Test acc | Notes |
|---|---|---|
| LR_raw_max200 | **92.66%** | verbatim friend setup; lbfgs hits iter limit on raw features |
| LR_scaled_max2000 | **96.19%** | same LR but standard-scaled features + more iters |
| MLP_64x2 | **95.47%** | e66, 8,472 params |
| MLP_128x2 | **97.83%** | e72, 25,112 params |
| MLP_256x3 | **99.56%** | e66, 148,760 params |

## Per-letter F1 — best model `MLP_256x3`
| Letter | Support | Precision | Recall | F1 |
|---|---|---|---|---|
| a | 152 | 99.3% | 98.7% | 99.0% |
| b | 69 | 100.0% | 97.1% | 98.5% |
| c | 141 | 100.0% | 100.0% | 100.0% |
| d | 84 | 100.0% | 98.8% | 99.4% |
| e | 71 | 100.0% | 97.2% | 98.6% |
| f | 63 | 100.0% | 100.0% | 100.0% |
| g | 103 | 100.0% | 100.0% | 100.0% |
| h | 106 | 100.0% | 100.0% | 100.0% |
| i | 92 | 100.0% | 100.0% | 100.0% |
| k | 70 | 100.0% | 100.0% | 100.0% |
| l | 185 | 100.0% | 99.5% | 99.7% |
| m | 72 | 100.0% | 100.0% | 100.0% |
| n | 79 | 100.0% | 100.0% | 100.0% |
| o | 106 | 98.1% | 100.0% | 99.1% |
| p | 116 | 100.0% | 100.0% | 100.0% |
| q | 83 | 98.8% | 100.0% | 99.4% |
| r | 128 | 99.2% | 98.4% | 98.8% |
| s | 127 | 98.4% | 100.0% | 99.2% |
| t | 90 | 100.0% | 100.0% | 100.0% |
| u | 168 | 99.4% | 100.0% | 99.7% |
| v | 87 | 100.0% | 98.9% | 99.4% |
| w | 87 | 98.9% | 100.0% | 99.4% |
| x | 85 | 98.8% | 100.0% | 99.4% |
| y | 128 | 99.2% | 100.0% | 99.6% |

**Macro avg:** P=99.6%  R=99.5%  F1=99.6%

## Top confusions (counts ≥5)
_(none)_